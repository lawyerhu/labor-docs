"use client";

import { ArrowLeft, ArrowRight, Building2, Gavel, Scale, UserRound } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { api, CaseRecord, CaseStage, PartySide } from "@/lib/api";

export default function NewCasePage() {
  const router = useRouter();
  const [stage, setStage] = useState<CaseStage>("litigation");
  const [side, setSide] = useState<PartySide>("employer");
  const [title, setTitle] = useState("不服劳动仲裁裁决");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault(); setLoading(true); setError("");
    try {
      const record = await api<CaseRecord>("/api/cases", { method: "POST", body: JSON.stringify({ title, case_stage: stage, party_side: side }) });
      router.push(`/cases/${record.id}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建失败"); setLoading(false); }
  }

  return (
    <main className="setup-page" id="main-content">
      <header className="setup-header"><Link href="/dashboard" className="text-link"><ArrowLeft size={17} /> 返回案件</Link><span>新建案件</span><span /></header>
      <form className="setup-form" onSubmit={submit}>
        <div className="setup-progress"><span className="active" /><span /><span /><small>1 / 3</small></div>
        <div className="eyebrow"><span /> 两项必要信息</div>
        <h1>先确定文书方向</h1>
        <p className="setup-lead">这两项决定使用哪套模板。其他信息都可以稍后补充，不会阻止生成正式稿。</p>

        <fieldset className="choice-field"><legend>当前处于哪个阶段？</legend><div className="choice-grid">
          <button type="button" className={`choice-card ${stage === "arbitration" ? "selected" : ""}`} onClick={() => { setStage("arbitration"); setTitle("劳动争议仲裁申请"); }}><span className="choice-icon"><Gavel /></span><span><strong>准备申请劳动仲裁</strong><small>还没有仲裁裁决书</small></span><i /></button>
          <button type="button" className={`choice-card ${stage === "litigation" ? "selected" : ""}`} onClick={() => { setStage("litigation"); setTitle("不服劳动仲裁裁决"); }}><span className="choice-icon"><Scale /></span><span><strong>仲裁后准备起诉</strong><small>已经取得仲裁裁决</small></span><i /></button>
        </div></fieldset>

        <fieldset className="choice-field"><legend>申请人或原告是哪一方？</legend><div className="choice-grid">
          <button type="button" className={`choice-card ${side === "worker" ? "selected" : ""}`} onClick={() => setSide("worker")}><span className="choice-icon"><UserRound /></span><span><strong>劳动者</strong><small>本人或代理人使用</small></span><i /></button>
          <button type="button" className={`choice-card ${side === "employer" ? "selected" : ""}`} onClick={() => setSide("employer")}><span className="choice-icon"><Building2 /></span><span><strong>用人单位</strong><small>公司负责人或经办人使用</small></span><i /></button>
        </div></fieldset>

        <label className="field setup-title"><span>案件名称</span><input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} required /><small>仅用于工作台识别，不写入正式文书。</small></label>
        {error && <div className="form-error">{error}</div>}
        <button className="button button-primary button-large setup-submit" disabled={loading}>{loading ? "正在创建…" : <>创建并开始整理 <ArrowRight size={18} /></>}</button>
      </form>
    </main>
  );
}
