"use client";

import { ArrowLeft, ArrowRight, ShieldCheck, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api, CaseRecord, CaseStage, PartySide } from "@/lib/api";

export default function NewCasePage() {
  const router = useRouter();
  const [facts, setFacts] = useState("");
  const [claims, setClaims] = useState("");
  const [stage, setStage] = useState<CaseStage | "">("");
  const [side, setSide] = useState<PartySide | "">("");
  const [consent, setConsent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!consent) { setError("请先同意将本次案情文本发送至大模型和元典进行分析"); return; }
    setLoading(true); setError("");
    try {
      const record = await api<CaseRecord>("/api/cases", {
        method: "POST",
        body: JSON.stringify({ case_stage: stage, party_side: side, facts, claims_text: claims }),
      });
      await api(`/api/cases/${record.id}/chat`, {
        method: "POST",
        body: JSON.stringify({ message: "开始分析", consent_cloud_processing: true }),
      });
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
      <h1>先把发生的事说清楚</h1>
      <p className="setup-lead">不需要填写姓名、身份证号、手机号或详细住址。系统分析后只会集中追问一次。</p>
      <div className="field-grid">
        <label className="field"><span>当前阶段</span><select value={stage} onChange={(event) => setStage(event.target.value as CaseStage)} required><option value="">请选择</option><option value="arbitration">劳动仲裁</option><option value="litigation">仲裁后起诉</option></select></label>
        <label className="field"><span>申请人/原告一方</span><select value={side} onChange={(event) => setSide(event.target.value as PartySide)} required><option value="">请选择</option><option value="worker">劳动者</option><option value="employer">公司/用人单位</option></select></label>
      </div>
      <label className="field full"><span>案情经过</span><textarea rows={9} value={facts} onChange={(event) => setFacts(event.target.value)} placeholder="例如：何时入职、岗位和工资、发生了什么争议、何时离职或解除、仲裁结果和送达情况……" required /></label>
      <label className="field full"><span>你的诉求</span><textarea rows={5} value={claims} onChange={(event) => setClaims(event.target.value)} placeholder="例如：不服仲裁裁决中的违约金，要求判决无需支付或调低至……" required /></label>
      <div className="privacy-note"><ShieldCheck size={18} /><span>姓名、证件号码、联系方式等敏感信息将在 Word 中显示为明确的“待填入”项，由你下载后本地填写。</span></div>
      <label className="consent-check"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>同意将本次输入文本发送至配置的大模型，并按需向元典发送最小化检索词</span></label>
      {error && <div className="form-error">{error}</div>}
      <button className="button button-primary button-large" disabled={loading || !stage || !side || !facts.trim() || !claims.trim()}>{loading ? "正在分析案情…" : <><Sparkles size={18} /> 开始分析 <ArrowRight size={18} /></>}</button>
    </form>
  </main>;
}
