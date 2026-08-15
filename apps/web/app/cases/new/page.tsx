"use client";

import { ArrowLeft, ArrowRight, FileText, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api, CaseRecord, CaseStage, PartySide } from "@/lib/api";

export default function NewCasePage() {
  const router = useRouter();
  const [description, setDescription] = useState("");
  const [claims, setClaims] = useState("");
  const [stage, setStage] = useState<CaseStage | "">("");
  const [side, setSide] = useState<PartySide | "">("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [consentCloud, setConsentCloud] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!consentCloud) {
      setError("请先勾选同意将案情发送至云端处理");
      return;
    }
    setLoading(true); setError("");
    try {
      const record = await api<CaseRecord>("/api/cases", {
        method: "POST",
        body: JSON.stringify({ case_stage: stage, party_side: side, facts: description, claims_text: claims }),
      });
      try {
        await api(`/api/cases/${record.id}/chat`, {
          method: "POST",
          body: JSON.stringify({
            message: "请分析案情和诉请，指出确有必要补充的信息，并结合类案整理建议提交的证据。已有证据可以提交，没有的证据不影响继续生成。",
            consent_cloud_processing: consentCloud,
          }),
        });
      } catch {
        // The workspace exposes a retry button if an external analysis service is unavailable.
      }
      router.push(`/cases/${record.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "案件分析失败，请稍后重试");
      setLoading(false);
    }
  }

  return <main className="intake-page" id="main-content">
    <header className="setup-header"><Link href="/dashboard" className="text-link"><ArrowLeft size={17} /> 返回案件</Link><span>创建案件</span><span /></header>
    <form className="intake-card" onSubmit={submit}>
      <div className="eyebrow"><span /> 案件分析</div>
      <h1>先确定案件方向</h1>
       <p className="setup-lead">先提交案情和诉请，系统会先做一次全案分析，再集中询问缺失信息并提示类案常见证据。</p>
      <div className="field-grid">
        <label className="field"><span>当前阶段</span><select value={stage} onChange={(event) => setStage(event.target.value as CaseStage)} required><option value="">请选择</option><option value="arbitration">劳动仲裁</option><option value="litigation">仲裁后起诉</option></select></label>
        <label className="field"><span>申请人/原告一方</span><select value={side} onChange={(event) => setSide(event.target.value as PartySide)} required><option value="">请选择</option><option value="worker">劳动者</option><option value="employer">公司/用人单位</option></select></label>
      </div>
       <label className="field full"><span>案情说明 <small>尽量填写完整</small></span><textarea rows={5} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="例如：请尽量填写入职、离职或解除时间、岗位、工资、争议经过、仲裁结果等事实。" /></label>
       <label className="field full"><span>诉请或请求 <small>尽量填写完整</small></span><textarea rows={4} value={claims} onChange={(event) => setClaims(event.target.value)} placeholder="例如：请写明具体请求事项、金额、计算期间、计算依据，以及仲裁支持或驳回的部分。" /></label>
       <div className="privacy-note"><FileText size={18} /><span>系统先分析案情、诉请和类案举证风险，再根据你实际提交的材料选择性编排证据目录。</span></div>
       <label className="consent-line"><input type="checkbox" checked={consentCloud} onChange={(event) => setConsentCloud(event.target.checked)} /><span>我同意将本案件的案情说明、诉请和上传材料的内容发送至云端大模型和元典法律数据库，用于本次分析、检索与文书撰写；材料仅用于本案件，不会用于训练。</span></label>
      {error && <div className="form-error">{error}</div>}
       <button className="button button-primary button-large" disabled={loading || !stage || !side || !consentCloud}>{loading ? "正在分析案情…" : <><Sparkles size={18} /> 分析案情并继续 <ArrowRight size={18} /></>}</button>
    </form>
  </main>;
}
