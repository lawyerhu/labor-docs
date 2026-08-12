"use client";

import { AlertCircle, ArrowLeft, Check, Download, FileText, Info, LoaderCircle, Paperclip, Sparkles, Trash2, UploadCloud } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, Artifact, CaseRecord, EvidenceItem, GenerationJob, uploadEvidence } from "@/lib/api";

const stageLabels: Record<string, string> = {
  queued: "等待处理",
  extracting: "正在读取材料",
  analyzing: "大模型正在理解材料",
  retrying: "正在重试识别",
  complete: "识别完成",
  failed: "识别暂未完成",
};

const generationStageLabels: Record<string, string> = {
  queued: "等待开始生成",
  preparing: "正在准备案件材料",
  processing: "正在生成正式材料",
  completed: "生成完成",
  failed: "生成暂未完成",
};

export default function CaseWorkspacePage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [record, setRecord] = useState<CaseRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [uploads, setUploads] = useState<Record<string, number>>({});
  const [generation, setGeneration] = useState<{ stage: string; progress: number } | null>(null);
  const [consentCloudProcessing, setConsentCloudProcessing] = useState(false);

  async function reload() {
    try { setRecord(await api<CaseRecord>(`/api/cases/${params.id}`)); }
    catch (reason) {
      const typed = reason as Error & { status?: number };
      if (typed.status === 401) router.replace("/login");
      else setError(typed.message);
    }
  }

  useEffect(() => { reload(); }, [params.id]);
  const materialProcessing = Boolean(record?.evidence?.some((item) => item.status === "processing"));
  useEffect(() => {
    if (!materialProcessing) return;
    const timer = window.setInterval(reload, 1500);
    return () => window.clearInterval(timer);
  }, [materialProcessing, params.id]);

  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return;
    if (!consentCloudProcessing) {
      setError("请先同意将案情说明和材料文字发送至配置的大模型处理");
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    setBusy(true); setError("");
    try {
      for (const file of Array.from(files)) {
        setUploads((current) => ({ ...current, [file.name]: 1 }));
        await uploadEvidence(params.id, file, consentCloudProcessing, (progress) => {
          setUploads((current) => ({ ...current, [file.name]: progress }));
        });
        setUploads((current) => { const next = { ...current }; delete next[file.name]; return next; });
        await reload();
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "材料上传失败");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function deleteEvidence(item: EvidenceItem) {
    if (!window.confirm(`确定删除“${item.name || item.original_name}”吗？`)) return;
    setBusy(true); setError("");
    try {
      await api(`/api/cases/${params.id}/evidence/${item.id}`, { method: "DELETE" });
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除失败");
    } finally { setBusy(false); }
  }

  async function generate() {
    if (!consentCloudProcessing) {
      setError("请先同意将案情说明和材料文字发送至配置的大模型处理");
      return;
    }
    setBusy(true); setError(""); setGeneration({ stage: "正在创建生成任务", progress: 3 });
    try {
      const result = await api<{ artifacts?: Artifact[]; job_id?: string }>(`/api/cases/${params.id}/generate`, {
        method: "POST",
        body: JSON.stringify({ consent_cloud_processing: true }),
      });
      if (result.job_id) {
        for (let attempt = 0; attempt < 180; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          const job = await api<GenerationJob>(`/api/cases/${params.id}/generation-jobs/${result.job_id}`);
          setGeneration({ stage: generationStageLabels[job.stage] || job.stage || "正在处理", progress: job.progress || 5 });
          if (job.status === "failed") throw new Error(job.error || "生成失败");
          if (job.status === "completed") break;
          if (attempt === 179) throw new Error("生成时间较长，请稍后刷新案件");
        }
      }
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "生成失败");
    } finally {
      setBusy(false); setGeneration(null);
    }
  }

  if (!record) return <main className="workspace-loading"><LoaderCircle className="spin" /> 正在打开案件…</main>;
  const summary = String(record.data?.analysis?.summary || record.data?.intake?.facts || "");

  return <main className="linear-case" id="main-content">
    <header className="setup-header"><Link href="/dashboard" className="text-link"><ArrowLeft size={17} /> 返回案件</Link><strong>{record.title}</strong><span /></header>
    <section className="linear-content">
      {error && <div className="notice notice-error"><AlertCircle size={17} />{error}<button onClick={() => setError("")}>关闭</button></div>}

      <div className="analysis-card">
        <div className="eyebrow"><span /> 大模型全案撰写</div>
        <h1>上传现有材料，直接生成</h1>
        <p>{summary || "不必先填写大量表格。系统会阅读案情说明和全部材料，形成诉请、事实理由、证据名称、来源与证明目的。"}</p>
        <div className="analysis-done"><Check size={18} /> 缺失内容只在确有必要时标为“待填入”，不会阻止生成。</div>
      </div>

      <div className="evidence-card">
        <div><div className="eyebrow"><span /> 材料识别</div><h2>上传你实际持有的材料</h2><p>大模型阅读内容后命名证据并填写来源、证明目的；随后按连续页码统一排版。</p></div>
        <button className="upload-zone" onClick={() => fileRef.current?.click()} disabled={busy}><UploadCloud size={25} /><strong>选择材料</strong><span>PDF、图片、Word、Excel；单个不超过 50MB</span><input ref={fileRef} type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,.xls,.xlsx" onChange={(event) => uploadFiles(event.target.files)} hidden /></button>
        {Object.entries(uploads).map(([name, progress]) => <ProgressBlock key={name} label={`正在上传：${name}`} progress={progress} />)}
        {!record.evidence?.length
          ? <div className="empty-evidence"><Paperclip size={20} /><span>可以不上传材料；系统仍会依据现有说明生成含待填项的正式稿。</span></div>
          : <div className="evidence-list">{record.evidence.map((item, index) => <EvidenceCard item={item} index={index} key={item.id} onDelete={() => deleteEvidence(item)} />)}</div>}
        <label className="consent-line"><input type="checkbox" checked={consentCloudProcessing} onChange={(event) => setConsentCloudProcessing(event.target.checked)} /><span>同意将本案说明和材料文字发送至配置的大模型，用于本次识别和文书撰写。</span></label>
      </div>

      <div className="generation-card">
        <div><div className="eyebrow"><span /> 全案生成</div><h2>大模型撰写，程序确定排版</h2></div>
        <div className="output-preview"><OutputRow name={record.case_stage === "litigation" ? "民事起诉状（要素式、普通式）" : "劳动人事争议仲裁申请书"} type="DOCX" /><OutputRow name="证据目录（横向五列）" type="DOCX" /><OutputRow name="证据材料（连续页码）" type="PDF" muted={!record.evidence?.length} /></div>
        {generation && <ProgressBlock label={generation.stage} progress={generation.progress} />}
        <button className="button button-primary button-large generate-button" onClick={generate} disabled={busy || materialProcessing || (record.access_status === "locked" && !record.unlimited_generation) || (!record.unlimited_generation && record.generation_count >= 3)}>{busy ? <><LoaderCircle className="spin" /> 正在生成…</> : <><Sparkles size={19} /> 大模型撰写并生成正式材料</>}<span>{record.unlimited_generation ? `${record.generation_count} 次 · 不限量` : `${record.generation_count}/3 次`}</span></button>
        {materialProcessing && <div className="form-hint">材料仍在识别，完成后即可生成。</div>}
        {record.access_status === "locked" && !record.unlimited_generation && <div className="form-error">该案件需要兑换码后才能生成。</div>}
        {!!record.artifacts?.length && <div className="downloads">{record.artifacts.map((artifact) => <a className="download-row" href={`/api/cases/${record.id}/artifacts/${artifact.id}`} key={artifact.id}><FileText size={19} /><span><strong>{artifact.filename}</strong><small>点击下载</small></span><Download size={18} /></a>)}</div>}
        <div className="legal-note"><Info size={17} /><p>系统会结合已核验的法律检索结果进行撰写；无法核验的具体条款不会被编造。</p></div>
      </div>
    </section>
  </main>;
}

function EvidenceCard({ item, index, onDelete }: { item: EvidenceItem; index: number; onDelete: () => void }) {
  const processing = item.status === "processing";
  const failed = item.status === "failed";
  return <article className="evidence-item">
    <div className="evidence-number">{index + 1}</div>
    <div className="evidence-summary">
      <strong>{processing ? "正在阅读材料" : item.name || "材料待分析"}</strong>
      <small>原文件：{item.original_name}</small>
      {processing
        ? <ProgressBlock label={stageLabels[item.processing_stage] || "正在处理材料"} progress={item.processing_progress || 10} compact />
        : <dl><div><dt>来源</dt><dd>{item.source || "将在全案生成时判断"}</dd></div><div><dt>证明目的</dt><dd>{item.purpose || (failed ? "本次识别未完成，将在全案生成时重新判断" : "将在全案生成时判断")}</dd></div></dl>}
    </div>
    <div className="evidence-actions"><button className="icon-button danger" onClick={onDelete} aria-label={`删除${item.name || item.original_name}`}><Trash2 size={17} /></button></div>
  </article>;
}

function ProgressBlock({ label, progress, compact = false }: { label: string; progress: number; compact?: boolean }) {
  const safe = Math.max(0, Math.min(100, Math.round(progress)));
  return <div className={`task-progress ${compact ? "compact" : ""}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={safe}><div><span>{label}</span><strong>{safe}%</strong></div><div className="progress-track"><span style={{ width: `${safe}%` }} /></div></div>;
}

function OutputRow({ name, type, muted }: { name: string; type: string; muted?: boolean }) {
  return <div className={`output-row ${muted ? "muted" : ""}`}><span className="file-type">{type}</span><strong>{name}</strong>{muted ? <Info size={17} /> : <Check size={17} />}</div>;
}
