"use client";

import { ArrowLeft, ArrowRight, FileText, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api, CaseRecord, CaseStage, PartySide } from "@/lib/api";

export default function NewCasePage() {
  const router = useRouter();
  const [description, setDescription] = useState("");
  const [stage, setStage] = useState<CaseStage | "">("");
  const [side, setSide] = useState<PartySide | "">("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true); setError("");
    try {
      const record = await api<CaseRecord>("/api/cases", {
        method: "POST",
        body: JSON.stringify({ case_stage: stage, party_side: side, facts: description, claims_text: "" }),
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
      <h1>先确定案件方向</h1>
      <p className="setup-lead">只需选择阶段和身份。可以补充一句案情，也可以创建后直接上传材料。</p>
      <div className="field-grid">
        <label className="field"><span>当前阶段</span><select value={stage} onChange={(event) => setStage(event.target.value as CaseStage)} required><option value="">请选择</option><option value="arbitration">劳动仲裁</option><option value="litigation">仲裁后起诉</option></select></label>
        <label className="field"><span>申请人/原告一方</span><select value={side} onChange={(event) => setSide(event.target.value as PartySide)} required><option value="">请选择</option><option value="worker">劳动者</option><option value="employer">公司/用人单位</option></select></label>
      </div>
      <label className="field full"><span>补充说明 <small>选填</small></span><textarea rows={5} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="例如：公司不服仲裁裁决中的违约金，希望法院调低或判决无需支付。" /></label>
      <div className="privacy-note"><FileText size={18} /><span>大模型会综合这段说明和你随后上传的材料，撰写诉请、事实理由及证据目录。</span></div>
      {error && <div className="form-error">{error}</div>}
      <button className="button button-primary button-large" disabled={loading || !stage || !side}>{loading ? "正在创建…" : <><Sparkles size={18} /> 创建并上传材料 <ArrowRight size={18} /></>}</button>
    </form>
  </main>;
}
