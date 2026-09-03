"use client";

import { AlertCircle, ArrowLeft, Download, FileText, ListChecks, LoaderCircle, Paperclip, RefreshCw, Send, ShieldCheck, Sparkles, Trash2, UploadCloud } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api, Artifact, CaseRecord, CaseStatus, EvidenceItem, EvidenceManifestRow, GenerationJob, uploadEvidence } from "@/lib/api";

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

const CLIENT_VERSION = "review-v1";
const UNSUPPORTED_IMAGE_ERROR = /(?:ERROR:\s*)?Cannot read\s+[^\n]*?\(this model does not support image input\)\.?\s*(?:Inform the user\.)?/gi;

function cleanModelText(value: unknown): string {
  return String(value ?? "")
    .replace(UNSUPPORTED_IMAGE_ERROR, "")
    .replace(/^\s*ERROR:\s*$/gim, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export default function CaseWorkspacePage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [record, setRecord] = useState<CaseRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [uploads, setUploads] = useState<Record<string, number>>({});
  const [generation, setGeneration] = useState<{ stage: string; progress: number } | null>(null);
  const [generationJobId, setGenerationJobId] = useState<string | null>(null);
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [supplement, setSupplement] = useState("");
  const [reviewOpen, setReviewOpen] = useState(false);
  const reloadSeq = useRef(0);

  async function reload() {
    const sequence = ++reloadSeq.current;
    try {
      const fresh = await api<CaseRecord>(`/api/cases/${params.id}`);
      if (sequence === reloadSeq.current) setRecord(fresh);
    } catch (reason) {
      if (sequence !== reloadSeq.current) return;
      const typed = reason as Error & { status?: number };
      if (typed.status === 401) router.replace("/login");
      else setError(typed.message);
    }
  }

  useEffect(() => { reload(); }, [params.id]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let backoff = 1500;
    let last: CaseStatus | null = null;

    async function tick() {
      let status: CaseStatus | null = null;
      try {
        status = await api<CaseStatus>(`/api/cases/${params.id}/status`);
      } catch (reason) {
        if (cancelled) return;
        const typed = reason as Error & { status?: number };
        if (typed.status === 401) {
          router.replace("/login");
          return;
        }
        backoff = Math.min(backoff + 1000, 5000);
        timer = window.setTimeout(tick, backoff);
        return;
      }
      if (cancelled || !status) return;
      const job = status.job;
      if (job) {
        setBusy(true);
        setGeneration({ stage: generationStageLabels[job.stage] || job.stage || "正在处理", progress: job.progress || 5 });
        if (job.status === "failed" || job.status === "completed") {
          setBusy(false);
          setGeneration(null);
          setGenerationJobId(null);
          if (job.status === "failed") setError(job.error || "生成失败");
          await reload();
          return;
        }
      } else if (last?.job) {
        setBusy(false);
        setGeneration(null);
        setGenerationJobId(null);
        await reload();
        return;
      }
      const settled = last
        ? (last.analysis_pending && !status.analysis_pending) || (last.materials_processing && !status.materials_processing)
        : false;
      if (status.analysis_pending || status.materials_processing || job) {
        if (settled) await reload();
        last = status;
        backoff = Math.min(backoff + 1000, 5000);
        timer = window.setTimeout(tick, backoff);
        return;
      }
      // A fast task may finish before the first status poll observes the
      // processing state. Refresh once whenever the API reports an idle case
      // so the details view cannot remain on a stale uploading card.
      last = null;
      await reload();
    }

    void tick();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [params.id, generationJobId, router]);

  const materialProcessing = Boolean(record?.evidence?.some((item) => item.status === "queued" || item.status === "processing"));
  const analysisPending = record?.data?.analysis?.status === "pending";

  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true); setError("");
    try {
      for (const file of Array.from(files)) {
        setUploads((current) => ({ ...current, [file.name]: 1 }));
        await uploadEvidence(params.id, file, true, (progress) => {
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

  async function analyzeCase(message = "请重新分析案情和诉请，指出确有必要补充的信息，并结合类案整理建议提交的证据。") {
    setAnalysisBusy(true); setBusy(true); setError("");
    try {
      await api(`/api/cases/${params.id}/chat`, {
        method: "POST",
        body: JSON.stringify({ message, consent_cloud_processing: true }),
      });
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "案件分析失败，请稍后重试");
    } finally {
      setAnalysisBusy(false); setBusy(false);
    }
  }

  async function submitSupplement() {
    const message = supplement.trim();
    if (!message) {
      setError("请填写需要补充的信息");
      return;
    }
    await analyzeCase(message);
    setSupplement("");
  }

  async function deleteCase() {
    if (!window.confirm("确定删除这个案件及其材料、生成文件吗？删除后无法恢复。")) return;
    setBusy(true); setError("");
    try {
      await api(`/api/cases/${params.id}`, { method: "DELETE" });
      router.replace("/dashboard");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "案件删除失败");
      setBusy(false);
    }
  }

  async function generate() {
    let trackingStarted = false;
    setBusy(true); setError(""); setGeneration({ stage: "正在创建生成任务", progress: 3 });
    try {
      const result = await api<{ artifacts?: Artifact[]; job_id?: string }>(`/api/cases/${params.id}/generate`, {
        method: "POST",
        headers: { "X-Client-Version": CLIENT_VERSION },
        body: JSON.stringify({ consent_cloud_processing: true }),
      });
      if (result.job_id) {
        trackingStarted = true;
        setGenerationJobId(result.job_id);
        return;
      }
      await reload();
    } catch (reason) {
      const typed = reason as Error & { status?: number; body?: any };
      if (typed.status === 409 && typed.body?.needs_confirmation) {
        setReviewOpen(true);
        setError("案情或材料有更新，请先核对并确认后再生成");
        setBusy(false);
        setGeneration(null);
        return;
      }
      if (typed.status === 409) {
        const active = await api<GenerationJob | null>(`/api/cases/${params.id}/generation-jobs/active`).catch(() => null);
        if (active) {
          trackingStarted = true;
          setGeneration({ stage: generationStageLabels[active.stage] || active.stage || "正在处理", progress: active.progress || 5 });
          setGenerationJobId(active.id);
          return;
        }
      }
      setError(typed.message || "生成失败");
    } finally {
      if (!trackingStarted) {
        setBusy(false);
        setGeneration(null);
      }
    }
  }

  async function confirmAndGenerate(facts: string, claims: string, manifest: EvidenceManifestRow[]) {
    setBusy(true); setError("");
    try {
      await api(`/api/cases/${params.id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ facts, claims_text: claims, evidence_manifest: manifest, consent_cloud_processing: true }),
      });
      await reload();
      await generate();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "确认失败，请稍后重试");
      setBusy(false);
    }
  }

  async function retryEvidence(item: EvidenceItem) {
    setBusy(true); setError("");
    try {
      await api(`/api/cases/${params.id}/evidence/${item.id}/retry`, {
        method: "POST",
        body: JSON.stringify({ consent_cloud_processing: true }),
      });
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重试失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  async function downloadArtifact(artifact: Artifact) {
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/cases/${record!.id}/artifacts/${artifact.id}`, { credentials: "include" });
      if (!response.ok) {
        let message = `下载失败（HTTP ${response.status}）`;
        try {
          const body = await response.json();
          message = body.detail ?? message;
        } catch { /* keep default message */ }
        if (response.status === 401) router.replace("/login");
        throw new Error(message);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "下载失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  if (!record) return <main className="workspace-loading"><LoaderCircle className="spin" /> 正在打开案件…</main>;
  const summary = cleanModelText(record.data?.analysis?.summary || record.data?.intake?.facts || "");
  const analysis = record.data?.analysis as Record<string, any> | undefined;
  const questions = Array.isArray(analysis?.follow_up_questions)
    ? analysis.follow_up_questions.map(cleanModelText).filter(Boolean)
    : [];
  const evidenceRequirements = Array.isArray(record.data?.evidence_requirements)
    ? record.data.evidence_requirements.filter((item: unknown) => item && typeof item === "object")
    : record.evidence_gaps.map((item) => ({ suggested_evidence: item }));

  return <main className="linear-case" id="main-content">
    <header className="setup-header"><Link href="/dashboard" className="text-link"><ArrowLeft size={17} /> 返回案件</Link><strong>{record.title}</strong><button className="icon-button danger" onClick={deleteCase} disabled={busy} aria-label="删除案件"><Trash2 size={17} /></button></header>
    <section className="linear-content">
      {error && <div className="notice notice-error"><AlertCircle size={17} />{error}<button onClick={() => setError("")}>关闭</button></div>}

      <div className="analysis-card">
        <div className="eyebrow"><span /> 案情与证据分析</div>
        <h1>{analysisPending ? "正在分析案情和诉请" : analysis ? "案情分析完成，补充必要信息" : "先分析案情和诉请"}</h1>
        <p>{summary || "系统会先读取案情和诉请，结合元典类案分析请求权、缺失信息和建议证据。"}</p>
        {analysisPending && <div className="analysis-done"><LoaderCircle size={18} className="spin" /> 正在调用模型和元典分析，请稍候。</div>}
        {evidenceRequirements.length > 0 && <div className="evidence-plan">
          <strong><ListChecks size={17} /> 类案和请求权提示的证据</strong>
          <ol>{evidenceRequirements.map((item: any, index: number) => <li key={`${String(item.suggested_evidence || item.evidence || item.name)}-${index}`}><b>{String(item.suggested_evidence || item.evidence || item.name || "建议材料")}</b><small>有则提交，没有也不影响继续生成</small></li>)}</ol>
        </div>}
        {questions.length > 0 && analysis?.round === 1 && <div className="analysis-followups">
          <strong>请一次性补充以下信息</strong>
          <ol>{questions.map((question: string, index: number) => <li key={`${question}-${index}`}>{question}</li>)}</ol>
          <textarea rows={4} value={supplement} onChange={(event) => setSupplement(event.target.value)} placeholder="可以一次性回答上面的多个问题，也可以说明暂时无法提供。" />
          <button className="button button-secondary" onClick={submitSupplement} disabled={analysisBusy}><Send size={16} /> 合并补充信息</button>
        </div>}
        {!analysisPending && (!analysis || analysis.analysis_status === "fallback") && <button className="button button-secondary" onClick={() => analyzeCase()} disabled={analysisBusy}><Sparkles size={17} /> {analysisBusy ? "正在分析…" : analysis ? "重新分析案情和诉请" : "开始分析案情和诉请"}</button>}
      </div>

      <div className="evidence-card">
        <div><div className="eyebrow"><span /> 材料识别</div><h2>上传你实际持有的材料</h2><p>大模型阅读内容后命名材料，生成时会结合全案编排证据目录；随后按连续页码统一排版。</p></div>
        <button className="upload-zone" onClick={() => fileRef.current?.click()} disabled={busy}><UploadCloud size={25} /><strong>选择材料</strong><span>PDF、图片、Word、Excel；单个不超过 50MB</span><input ref={fileRef} type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,.xls,.xlsx" onChange={(event) => uploadFiles(event.target.files)} hidden /></button>
        {Object.entries(uploads).map(([name, progress]) => <ProgressBlock key={name} label={`正在上传：${name}`} progress={progress} />)}
        {!record.evidence?.length
          ? <div className="empty-evidence"><Paperclip size={20} /><span>可以不上传材料；系统仍会依据现有说明生成含待填项的正式稿。</span></div>
          : <div className="evidence-list">{record.evidence.map((item, index) => <EvidenceCard item={item} index={index} key={item.id} onDelete={() => deleteEvidence(item)} onRetry={() => retryEvidence(item)} />)}</div>}
      </div>

      {!analysisPending && !materialProcessing && <GenerationReviewCard key={(record.evidence ?? []).map((item) => `${item.id}:${item.status}:${item.name}`).join("|")} record={record} busy={busy} open={reviewOpen} generation={generation} onGenerate={generate} onDownload={downloadArtifact} onConfirm={confirmAndGenerate} />}
    </section>
  </main>;
}

function EvidenceCard({ item, index, onDelete, onRetry }: { item: EvidenceItem; index: number; onDelete: () => void; onRetry: () => void }) {
  const processing = item.status === "queued" || item.status === "processing";
  const failed = item.status === "failed";
  const analysisError = cleanModelText(item.analysis?.error) || "本次识别未完成，请删除后重新上传。";
  return <article className="evidence-item">
    <div className="evidence-number">{index + 1}</div>
    <div className="evidence-summary">
      <strong>{processing ? "正在阅读材料" : item.name || "材料待分析"}</strong>
      <small>原文件：{item.original_name}</small>
      {processing
        ? <ProgressBlock label={stageLabels[item.processing_stage] || "正在处理材料"} progress={item.processing_progress || 10} compact />
         : failed && <div className="form-error">{analysisError}</div>}
    </div>
    <div className="evidence-actions">{failed && <button className="button button-small" onClick={onRetry}><RefreshCw size={15} /> 重试</button>}<button className="icon-button danger" onClick={onDelete} aria-label={`删除${item.name || item.original_name}`}><Trash2 size={17} /></button></div>
  </article>;
}

function buildManifestRows(record: CaseRecord): EvidenceManifestRow[] {
  return (record.evidence ?? []).map((item) => {
    const splits = Array.isArray((item.analysis as any)?.splits) ? (item.analysis as any).splits as Array<Record<string, unknown>> : [];
    if (splits.length === 1) {
      const split = splits[0];
      return {
        id: item.id,
        name: String(split.name || item.name || "材料"),
        purpose: String(split.purpose || item.purpose || ""),
        pages: String(split.page_range || ""),
        included: split.included !== false,
      };
    }
    return {
      id: item.id,
      name: item.name || "材料",
      purpose: item.purpose || "",
      pages: "",
      included: (item.analysis as any)?.selected_for_package !== false,
    };
  });
}

function GenerationReviewCard({ record, busy, open, generation, onGenerate, onDownload, onConfirm }: {
  record: CaseRecord;
  busy: boolean;
  open: boolean;
  generation: { stage: string; progress: number } | null;
  onGenerate: () => void;
  onDownload: (artifact: Artifact) => void;
  onConfirm: (facts: string, claims: string, manifest: EvidenceManifestRow[]) => void;
}) {
  const needsConfirmation = record.workflow?.needs_confirmation !== false;
  const intake = record.data?.intake as Record<string, any> | undefined;
  const [facts, setFacts] = useState(String(intake?.facts || ""));
  const [claims, setClaims] = useState(String(intake?.claims_text || ""));
  const [manifest, setManifest] = useState<EvidenceManifestRow[]>(() => buildManifestRows(record));
  const [expanded, setExpanded] = useState(open || needsConfirmation);

  useEffect(() => { setExpanded(open || needsConfirmation); }, [open, needsConfirmation]);

  function patchRow(id: string, patch: Partial<EvidenceManifestRow>) {
    setManifest((current) => current.map((row) => row.id === id ? { ...row, ...patch } : row));
  }

  const includedCount = manifest.filter((row) => row.included).length;
  const generationBlocked = (record.access_status === "locked" && !record.unlimited_generation)
    || (!record.unlimited_generation && record.generation_count >= 3);
  return <section className={`review-card ${needsConfirmation ? "pending" : "complete"}`}>
    <div className="review-heading" onClick={() => setExpanded((value) => !value)} role="button" tabIndex={0}>
      <ShieldCheck size={19} />
      <div><div className="eyebrow"><span /> 生成前核对</div><h2>{needsConfirmation ? "请核对案情、诉请与证据清单" : "已核对，可以直接生成"}</h2><p>{needsConfirmation ? "案情、诉请或材料最近有更新，需要重新确认后才开始生成。" : `共 ${manifest.length} 份材料、${includedCount} 份纳入证据目录；确认后可立即生成。`}</p></div>
    </div>
    {expanded && <div className="review-body">
      <label className="field full"><span>案情说明 <small>生成时将以此为准</small></span><textarea rows={4} value={facts} onChange={(event) => setFacts(event.target.value)} /></label>
      <label className="field full"><span>诉请或请求 <small>生成时将以此为准</small></span><textarea rows={3} value={claims} onChange={(event) => setClaims(event.target.value)} /></label>
      <div className="manifest-title"><strong>证据清单 <small>默认纳入全部材料；取消勾选后该材料不会出现在证据目录和证据材料中</small></strong></div>
      {manifest.length === 0
        ? <div className="form-hint">还没有上传材料，可以直接生成含待填项的正式稿。</div>
        : <div className="manifest-list">{manifest.map((row) => (
          <label className="manifest-row" key={row.id}>
            <input type="checkbox" checked={row.included} onChange={(event) => patchRow(row.id, { included: event.target.checked })} />
            <span className="manifest-name"><strong>{row.name}</strong><small>{row.purpose || "（未填写证明目的）"}</small></span>
            <span className="manifest-pages"><input value={row.pages} onChange={(event) => patchRow(row.id, { pages: event.target.value })} placeholder="页码，如 1-3" disabled={!row.included} /><em>不填则整份纳入</em></span>
          </label>
        ))}</div>}
      <div className="review-actions"><span>确认后系统将按此清单排版证据目录与证据材料。</span><button className="button button-primary" onClick={() => onConfirm(facts, claims, manifest)} disabled={busy || !facts.trim()}>{busy ? <><LoaderCircle className="spin" /> 正在确认并生成…</> : <><ShieldCheck size={16} /> 确认并生成</>}</button></div>
    </div>}
    {generation && <div className="review-runtime"><ProgressBlock label={generation.stage} progress={generation.progress} /></div>}
    {!needsConfirmation && !generation && !generationBlocked && <button className="button button-primary review-generate" onClick={onGenerate} disabled={busy}><Sparkles size={17} /> {record.artifacts?.length ? "重新生成材料" : "开始生成材料"}</button>}
    {record.access_status === "locked" && !record.unlimited_generation && <div className="form-error review-error">该案件需要兑换码后才能生成。</div>}
    {!record.unlimited_generation && record.generation_count >= 3 && <div className="form-error review-error">本案件最多成功生成3次。</div>}
    {!!record.artifacts?.length && <div className="downloads">{record.artifacts.map((artifact) => <button className="download-row" onClick={() => onDownload(artifact)} key={artifact.id} disabled={busy}><FileText size={19} /><span><strong>{artifact.filename}</strong><small>{artifact.outdated ? "案情或材料已更新，此文件可能不包含最新内容" : "点击下载"}</small></span>{artifact.outdated ? <span className="artifact-outdated">已过期</span> : <Download size={18} />}</button>)}</div>}
  </section>;
}

function ProgressBlock({ label, progress, compact = false }: { label: string; progress: number; compact?: boolean }) {
  const safe = Math.max(0, Math.min(100, Math.round(progress)));
  return <div className={`task-progress ${compact ? "compact" : ""}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={safe}><div><span>{label}</span><strong>{safe}%</strong></div><div className="progress-track"><span style={{ width: `${safe}%` }} /></div></div>;
}
