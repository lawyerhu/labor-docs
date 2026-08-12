"use client";

import { AlertCircle, ArrowLeft, Check, Download, FileText, Info, LoaderCircle, Paperclip, Save, Sparkles, Trash2, UploadCloud } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { FormEvent, useEffect, useRef, useState } from "react";
import { api, Artifact, CaseRecord, EvidenceItem } from "@/lib/api";

export default function CaseWorkspacePage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [record, setRecord] = useState<CaseRecord | null>(null);
  const [supplement, setSupplement] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function reload() {
    try { setRecord(await api<CaseRecord>(`/api/cases/${params.id}`)); }
    catch (reason) { const typed = reason as Error & { status?: number }; if (typed.status === 401) router.replace("/login"); else setError(typed.message); }
  }
  useEffect(() => { reload(); }, [params.id]);

  const analysis = record?.data?.analysis ?? {};
  const hasAnalysis = analysis.round === 1 || analysis.round === 2;
  const needsFollowUp = analysis.round === 1;

  async function startAnalysis() {
    setBusy(true); setError("");
    try {
      await api(`/api/cases/${params.id}/chat`, { method: "POST", body: JSON.stringify({ message: "开始分析", consent_cloud_processing: true }) });
      await reload();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "案件分析失败"); }
    finally { setBusy(false); }
  }

  async function submitSupplement(event: FormEvent) {
    event.preventDefault(); if (!supplement.trim()) return;
    setBusy(true); setError("");
    try {
      await api(`/api/cases/${params.id}/chat`, { method: "POST", body: JSON.stringify({ message: supplement, consent_cloud_processing: true }) });
      setSupplement(""); await reload();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "补充信息提交失败"); }
    finally { setBusy(false); }
  }

  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return; setBusy(true); setError("");
    try {
      for (const file of Array.from(files)) { const form = new FormData(); form.append("file", file); await api(`/api/cases/${params.id}/evidence`, { method: "POST", body: form }); }
      await reload();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "证据上传失败"); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  function editEvidence(id: string, key: keyof EvidenceItem, value: string) {
    setRecord((current) => current ? { ...current, evidence: current.evidence?.map((item) => item.id === id ? { ...item, [key]: value } : item) } : current);
  }
  async function saveEvidence(item: EvidenceItem) {
    setBusy(true); try { await api(`/api/cases/${params.id}/evidence/${item.id}`, { method: "PATCH", body: JSON.stringify({ name: item.name, source: item.source, purpose: item.purpose }) }); await reload(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "证据说明保存失败"); } finally { setBusy(false); }
  }
  async function deleteEvidence(item: EvidenceItem) {
    if (!window.confirm(`确定删除“${item.name}”吗？`)) return;
    setBusy(true); try { await api(`/api/cases/${params.id}/evidence/${item.id}`, { method: "DELETE" }); await reload(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败"); } finally { setBusy(false); }
  }

  async function generate() {
    setBusy(true); setError("");
    try {
      const result = await api<{ artifacts?: Artifact[]; job_id?: string }>(`/api/cases/${params.id}/generate`, { method: "POST" });
      if (result.job_id) {
        for (let attempt = 0; attempt < 120; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          const job = await api<{ status: string; error?: string }>(`/api/cases/${params.id}/generation-jobs/${result.job_id}`);
          if (job.status === "failed") throw new Error(job.error || "生成失败");
          if (job.status === "completed") break;
          if (attempt === 119) throw new Error("生成时间较长，请稍后刷新案件");
        }
      }
      await reload();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "生成失败"); }
    finally { setBusy(false); }
  }

  if (!record) return <main className="workspace-loading"><LoaderCircle className="spin" /> 正在打开案件…</main>;
  return <main className="linear-case" id="main-content">
    <header className="setup-header"><Link href="/dashboard" className="text-link"><ArrowLeft size={17} /> 返回案件</Link><strong>{record.title}</strong><span /></header>
    <section className="linear-content">
      {error && <div className="notice notice-error"><AlertCircle size={17} />{error}<button onClick={() => setError("")}>关闭</button></div>}
      <div className="analysis-card">
        <div className="eyebrow"><span /> 大模型案件分析</div>
        <h1>{!hasAnalysis ? "等待案件分析" : needsFollowUp ? "请集中补充一次" : "案件信息已整理"}</h1>
        <p>{analysis.summary || record.data?.intake?.facts}</p>
        {!hasAnalysis && <button className="button button-primary" onClick={startAnalysis} disabled={busy}>{busy ? "正在分析…" : "重新开始分析"}</button>}
        {analysis.claims_summary && <div className="analysis-claims"><strong>诉求理解</strong><p>{analysis.claims_summary}</p></div>}
        {typeof analysis.legal_analysis === "string" && analysis.legal_analysis && <div className="analysis-claims"><strong>元典辅助研判</strong><p>{analysis.legal_analysis}</p></div>}
        {needsFollowUp && <form onSubmit={submitSupplement} className="follow-up-form">
          <ol>{(analysis.follow_up_questions ?? []).map((question: string) => <li key={question}>{question}</li>)}</ol>
          <textarea rows={8} value={supplement} onChange={(event) => setSupplement(event.target.value)} placeholder="请尽量一次性回答以上问题；不知道的内容可写“不清楚”。" required />
          <button className="button button-primary" disabled={busy || !supplement.trim()}>提交补充并完成分析</button>
        </form>}
        {hasAnalysis && !needsFollowUp && <div className="analysis-done"><Check size={18} /> 不再继续追问。敏感身份信息保留为 Word 待填项。</div>}
      </div>

      <div className="evidence-card">
        <div><div className="eyebrow"><span /> 现有证据</div><h2>上传你实际持有的材料</h2><p>系统只编排成功上传的文件。建议材料不会被虚构成证据。</p></div>
        {!!analysis.evidence_suggestions?.length && <div className="suggestions"><strong>建议优先查找</strong>{analysis.evidence_suggestions.map((item: string) => <span key={item}>{item}</span>)}</div>}
        <button className="upload-zone" onClick={() => fileRef.current?.click()} disabled={busy}><UploadCloud size={25} /><strong>选择证据文件</strong><span>PDF、图片、Word、Excel；单个不超过 50MB</span><input ref={fileRef} type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,.xls,.xlsx" onChange={(event) => uploadFiles(event.target.files)} hidden /></button>
        {!record.evidence?.length ? <div className="empty-evidence"><Paperclip size={20} /><span>暂未上传证据；仍会生成空白证据目录 PDF，不生成空白证据材料。</span></div> : <div className="evidence-list">{record.evidence.map((item, index) => <article className="evidence-item" key={item.id}><div className="evidence-number">{index + 1}</div><div className="evidence-fields"><label><span>证据名称</span><input value={item.name} onChange={(event) => editEvidence(item.id, "name", event.target.value)} /></label><div className="evidence-row"><label><span>来源</span><input value={item.source} onChange={(event) => editEvidence(item.id, "source", event.target.value)} /></label><label className="purpose-field"><span>证明目的</span><input value={item.purpose} onChange={(event) => editEvidence(item.id, "purpose", event.target.value)} /></label></div></div><div className="evidence-actions"><button className="icon-button" onClick={() => saveEvidence(item)} aria-label="保存"><Save size={17} /></button><button className="icon-button danger" onClick={() => deleteEvidence(item)} aria-label="删除"><Trash2 size={17} /></button></div></article>)}</div>}
      </div>

      <div className="generation-card">
        <div><div className="eyebrow"><span /> 直接生成</div><h2>三份可下载材料</h2></div>
        <div className="output-preview"><OutputRow name={record.case_stage === "litigation" ? "民事起诉状" : "劳动人事争议仲裁申请书"} type="DOCX" /><OutputRow name="证据目录（横向五列）" type="PDF" /><OutputRow name="证据材料（连续页码）" type="PDF" muted={!record.evidence?.length} /></div>
        <button className="button button-primary button-large generate-button" onClick={generate} disabled={busy || !hasAnalysis || needsFollowUp || (record.access_status === "locked" && !record.unlimited_generation) || (!record.unlimited_generation && record.generation_count >= 3)}>{busy ? <><LoaderCircle className="spin" /> 正在生成…</> : <><Sparkles size={19} /> 生成正式材料</>}<span>{record.unlimited_generation ? `${record.generation_count} 次 · 不限量` : `${record.generation_count}/3 次`}</span></button>
        {record.access_status === "locked" && !record.unlimited_generation && <div className="form-error">该案件需要兑换码后才能生成。</div>}
        {!!record.artifacts?.length && <div className="downloads">{record.artifacts.map((artifact) => <a className="download-row" href={`/api/cases/${record.id}/artifacts/${artifact.id}`} key={artifact.id}><FileText size={19} /><span><strong>{artifact.filename}</strong><small>点击下载</small></span><Download size={18} /></a>)}</div>}
        <div className="legal-note"><Info size={17} /><p>元典检索用于核验管辖线索、请求权依据和相似案例。未核验内容不会被写成确定法条或案例结论。</p></div>
      </div>
    </section>
  </main>;
}

function OutputRow({ name, type, muted }: { name: string; type: string; muted?: boolean }) {
  return <div className={`output-row ${muted ? "muted" : ""}`}><span className="file-type">{type}</span><strong>{name}</strong>{muted ? <Info size={17} /> : <Check size={17} />}</div>;
}
