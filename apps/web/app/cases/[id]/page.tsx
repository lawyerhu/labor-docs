"use client";

import {
  AlertCircle,
  ArrowLeft,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  Download,
  FileCheck2,
  FileText,
  FolderOpen,
  Info,
  LoaderCircle,
  MessageSquareText,
  Paperclip,
  Plus,
  Save,
  Search,
  Send,
  Sparkles,
  Trash2,
  UploadCloud,
  UserRound,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, Artifact, CaseRecord, EvidenceItem, formatDate, getAt, setAt } from "@/lib/api";

type Tab = "guide" | "details" | "evidence" | "generate";

const tabs: { id: Tab; label: string; icon: typeof MessageSquareText }[] = [
  { id: "guide", label: "案情引导", icon: MessageSquareText },
  { id: "details", label: "信息确认", icon: FileCheck2 },
  { id: "evidence", label: "证据材料", icon: FolderOpen },
  { id: "generate", label: "生成下载", icon: Sparkles },
];

function Field({ label, path, data, setData, placeholder, type = "text" }: { label: string; path: string; data: Record<string, any>; setData: (value: Record<string, any>) => void; placeholder?: string; type?: string }) {
  return <label className="field"><span>{label}</span><input type={type} value={getAt(data, path)} onChange={(event) => setData(setAt(data, path, event.target.value))} placeholder={placeholder} /></label>;
}

export default function CaseWorkspacePage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const [record, setRecord] = useState<CaseRecord | null>(null);
  const [data, setData] = useState<Record<string, any>>({});
  const [tab, setTab] = useState<Tab>("guide");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [message, setMessage] = useState("");
  const [consent, setConsent] = useState(false);
  const [legalQuery, setLegalQuery] = useState("劳动争议相关法律依据");
  const [redeemCode, setRedeemCode] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function reload() {
    try {
      const result = await api<CaseRecord>(`/api/cases/${params.id}`);
      setRecord(result); setData(result.data ?? {});
    } catch (reason) {
      const typed = reason as Error & { status?: number };
      if (typed.status === 401) router.replace("/login"); else setError(typed.message);
    } finally { setLoading(false); }
  }

  useEffect(() => { reload(); }, [params.id]);

  const conversations = data.conversation ?? [];
  const claims = data.claims ?? [];
  const stepDone = useMemo(() => ({
    guide: conversations.length > 0,
    details: record?.readiness === "formal_complete",
    evidence: (record?.evidence?.length ?? 0) > 0,
    generate: (record?.artifacts?.length ?? 0) > 0,
  }), [conversations.length, record]);

  function flash(message: string) {
    setSuccess(message); window.setTimeout(() => setSuccess(""), 2600);
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault(); if (!message.trim()) return;
    const text = message; setMessage(""); setBusy(true); setError("");
    setData((current) => ({ ...current, conversation: [...(current.conversation ?? []), { role: "user", content: text }] }));
    try {
      const result = await api<{ data: Record<string, any>; reply: string }>(`/api/cases/${params.id}/chat`, { method: "POST", body: JSON.stringify({ message: text, consent_cloud_processing: consent }) });
      setData(result.data); await reload();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "发送失败"); await reload(); }
    finally { setBusy(false); }
  }

  async function saveDetails() {
    setBusy(true); setError("");
    try {
      const result = await api<CaseRecord>(`/api/cases/${params.id}`, { method: "PATCH", body: JSON.stringify({ data }) });
      setRecord((current) => ({ ...current!, ...result, evidence: current?.evidence, artifacts: current?.artifacts }));
      flash("案件信息已保存");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setBusy(false); }
  }

  function updateClaim(index: number, key: string, value: any) {
    const next = [...claims]; next[index] = { ...next[index], [key]: value }; setData({ ...data, claims: next });
  }

  function addClaim() {
    setData({ ...data, claims: [...claims, { kind: "other", title: "", amount: "", basis: "" }] });
  }

  function removeClaim(index: number) {
    setData({ ...data, claims: claims.filter((_: any, claimIndex: number) => claimIndex !== index) });
  }

  async function uploadFiles(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true); setError("");
    try {
      for (const file of Array.from(files)) {
        const form = new FormData(); form.append("file", file);
        await api(`/api/cases/${params.id}/evidence`, { method: "POST", body: form });
      }
      await reload(); flash(`${files.length}个文件已上传`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "上传失败"); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  async function saveEvidence(item: EvidenceItem) {
    setBusy(true);
    try {
      await api(`/api/cases/${params.id}/evidence/${item.id}`, { method: "PATCH", body: JSON.stringify({ name: item.name, source: item.source, purpose: item.purpose }) });
      await reload(); flash("证据说明已保存");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存失败"); }
    finally { setBusy(false); }
  }

  async function deleteEvidence(item: EvidenceItem) {
    if (!window.confirm(`确定删除“${item.name}”吗？原始上传副本也会删除。`)) return;
    setBusy(true);
    try { await api(`/api/cases/${params.id}/evidence/${item.id}`, { method: "DELETE" }); await reload(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "删除失败"); }
    finally { setBusy(false); }
  }

  function editEvidence(id: string, key: keyof EvidenceItem, value: string) {
    setRecord((current) => current ? { ...current, evidence: current.evidence?.map((item) => item.id === id ? { ...item, [key]: value } : item) } : current);
  }

  async function redeem() {
    setBusy(true); setError("");
    try { await api(`/api/cases/${params.id}/redeem`, { method: "POST", body: JSON.stringify({ code: redeemCode }) }); await reload(); flash("案件已解锁"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "兑换失败"); }
    finally { setBusy(false); }
  }

  async function generate() {
    setBusy(true); setError("");
    try {
      await saveDetails();
      const result = await api<{ readiness?: string; artifacts?: Artifact[]; job_id?: string; status?: string }>(`/api/cases/${params.id}/generate`, { method: "POST" });
      let generatedCount = result.artifacts?.length ?? 0;
      if (result.job_id) {
        for (let attempt = 0; attempt < 120; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          const job = await api<{ status: string; error?: string; result?: { artifacts?: Artifact[] } }>(`/api/cases/${params.id}/generation-jobs/${result.job_id}`);
          if (job.status === "failed") throw new Error(job.error || "生成失败");
          if (job.status === "completed") { generatedCount = job.result?.artifacts?.length ?? 0; break; }
          if (attempt === 119) throw new Error("生成时间较长，请稍后刷新案件查看结果");
        }
      }
      await reload(); flash(`已生成${generatedCount}个文件`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "生成失败"); }
    finally { setBusy(false); }
  }

  async function verifyLegalBasis() {
    if (!legalQuery.trim()) return;
    setBusy(true); setError("");
    try {
      const result = await api<{ snapshot: { verified: boolean } }>(`/api/cases/${params.id}/legal-search`, {
        method: "POST",
        body: JSON.stringify({ query: legalQuery.trim() }),
      });
      await reload();
      flash(result.snapshot.verified ? "法律依据已核验并保存" : "暂未完成核验，已保存查询快照");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "法律依据核验失败"); }
    finally { setBusy(false); }
  }

  if (loading) return <main className="workspace-loading"><LoaderCircle className="spin" /> 正在打开案件…</main>;
  if (!record) return <main className="workspace-loading">{error || "案件不存在"}</main>;

  return (
    <main className="workspace-shell" id="main-content">
      <header className="workspace-header">
        <Link href="/dashboard" className="icon-button" aria-label="返回案件列表"><ArrowLeft size={19} /></Link>
        <div className="workspace-title"><strong>{record.title}</strong><span>{record.case_stage === "arbitration" ? "劳动仲裁" : "仲裁后起诉"} · {record.party_side === "worker" ? "劳动者一方" : "用人单位一方"}</span></div>
        <div className={`readiness-pill ${record.readiness === "formal_complete" ? "complete" : "pending"}`}>{record.readiness === "formal_complete" ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}{record.readiness === "formal_complete" ? "正式稿·信息完整" : "正式稿·含待填项"}</div>
      </header>

      <div className="workspace-body">
        <aside className="workspace-nav">
          <div className="nav-caption">案件准备</div>
          {tabs.map((item, index) => { const Icon = item.icon; return <button key={item.id} className={`workspace-tab ${tab === item.id ? "active" : ""}`} onClick={() => setTab(item.id)}><span className="tab-index">{stepDone[item.id] ? <Check size={14} /> : index + 1}</span><Icon size={18} /><span>{item.label}</span><ChevronRight size={15} /></button>; })}
          <div className="expiry-note"><Info size={16} /><span>案件保存至<br /><strong>{formatDate(record.expires_at)}</strong></span></div>
        </aside>

        <section className="workspace-content">
          {error && <div className="notice notice-error"><AlertCircle size={17} />{error}<button onClick={() => setError("")}>关闭</button></div>}
          {success && <div className="notice notice-success"><CheckCircle2 size={17} />{success}</div>}

          {tab === "guide" && <div className="panel conversation-panel">
            <div className="panel-heading"><div><span className="section-kicker">案情引导</span><h1>先说说发生了什么</h1><p>可以像给同事讲述一样输入。系统会逐项提醒，但你随时可以跳过。</p></div><span className="ai-badge"><Bot size={16} /> 智能整理</span></div>
            <div className="conversation">
              {conversations.length === 0 && <div className="assistant-message"><span className="avatar"><Bot size={18} /></span><div><strong>从关键时间线开始</strong><p>请描述入职、离职或解除经过，以及仲裁结果。暂时不知道的内容可以直接说“不清楚”。</p><div className="prompt-chips"><button onClick={() => setMessage("我代表公司，已经收到劳动仲裁裁决，想对裁决中的部分金额起诉。")}>公司不服仲裁裁决</button><button onClick={() => setMessage("我是劳动者，准备申请劳动仲裁。")}>劳动者申请仲裁</button></div></div></div>}
              {conversations.map((entry: any, index: number) => entry.role === "user" ? <div className="user-message" key={index}>{entry.content}</div> : <div className="assistant-message" key={index}><span className="avatar"><Bot size={18} /></span><div><p>{entry.content}</p></div></div>)}
              {busy && <div className="assistant-message"><span className="avatar"><LoaderCircle className="spin" size={18} /></span><div><p>正在整理这段信息…</p></div></div>}
            </div>
            <form className="chat-composer" onSubmit={sendMessage}><textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder="描述案情，或回答上一项问题…" rows={3} /><div className="composer-bottom"><label className="consent-check"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>同意发送提取文本至配置的大模型</span></label><button className="send-button" disabled={!message.trim() || busy} aria-label="发送"><Send size={18} /></button></div></form>
            <button className="button button-secondary next-step" onClick={() => setTab("details")}>进入信息确认 <ChevronRight size={17} /></button>
          </div>}

          {tab === "details" && <div className="panel detail-panel">
            <div className="panel-heading"><div><span className="section-kicker">结构化确认</span><h1>核对将写入文书的内容</h1><p>空白字段会在文书中显示为“待填入”，不会阻止生成。</p></div><button className="button button-secondary" onClick={saveDetails} disabled={busy}><Save size={17} /> 保存</button></div>
            <DetailsForm record={record} data={data} setData={setData} claims={claims} updateClaim={updateClaim} addClaim={addClaim} removeClaim={removeClaim} />
            <div className="sticky-actions"><span>{record.missing_fields.length ? `仍有 ${record.missing_fields.length} 项待填` : "信息检查通过"}</span><button className="button button-primary" onClick={() => { saveDetails(); setTab("evidence"); }}>保存并继续 <ChevronRight size={17} /></button></div>
          </div>}

          {tab === "evidence" && <div className="panel evidence-panel">
            <div className="panel-heading"><div><span className="section-kicker">证据材料</span><h1>只整理实际上传的证据</h1><p>不会为尚未提供的材料创建虚假证据项。</p></div><span className="file-count">{record.evidence?.length ?? 0} / 20 个文件</span></div>
            <button className="upload-zone" onClick={() => fileRef.current?.click()} disabled={busy}><UploadCloud size={26} /><strong>选择材料上传</strong><span>PDF、图片、Word、Excel；单文件不超过50MB</span><input ref={fileRef} type="file" multiple accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,.xls,.xlsx" onChange={(event) => uploadFiles(event.target.files)} hidden /></button>
            {!record.evidence?.length ? <div className="empty-evidence"><Paperclip size={21} /><div><strong>当前没有证据</strong><p>系统会生成只有五列表头的证据目录，不会生成空白证据材料PDF。</p></div></div> : <div className="evidence-list">{record.evidence.map((item, index) => <article className="evidence-item" key={item.id}><div className="evidence-number">{String(index + 1).padStart(2, "0")}</div><div className="evidence-fields"><label><span>证据名称</span><input value={item.name} onChange={(event) => editEvidence(item.id, "name", event.target.value)} /></label><div className="evidence-row"><label><span>来源</span><input value={item.source} onChange={(event) => editEvidence(item.id, "source", event.target.value)} placeholder="例如：双方签署" /></label><label className="purpose-field"><span>证明目的</span><input value={item.purpose} onChange={(event) => editEvidence(item.id, "purpose", event.target.value)} placeholder="说明材料能够证明的具体事实" /></label></div><small>{item.original_name} · {(item.size_bytes / 1024 / 1024).toFixed(2)} MB · 原件副本已保留</small></div><div className="evidence-actions"><button className="icon-button" title="保存证据说明" aria-label="保存证据说明" onClick={() => saveEvidence(item)}><Save size={17} /></button><button className="icon-button danger" title="删除证据" aria-label="删除证据" onClick={() => deleteEvidence(item)}><Trash2 size={17} /></button></div></article>)}</div>}
            <button className="button button-primary next-step" onClick={() => setTab("generate")}>检查并生成 <ChevronRight size={17} /></button>
          </div>}

          {tab === "generate" && <div className="panel generate-panel">
            <div className="panel-heading"><div><span className="section-kicker">生成下载</span><h1>正式稿准备情况</h1><p>生成前请检查待填项；下载后仍可在 Word 中编辑。</p></div></div>
            <div className={`readiness-card ${record.readiness === "formal_complete" ? "complete" : "pending"}`}><span className="readiness-icon">{record.readiness === "formal_complete" ? <CheckCircle2 /> : <AlertCircle />}</span><div><strong>{record.readiness === "formal_complete" ? "正式稿·信息完整" : "正式稿·含待填项"}</strong><p>{record.readiness === "formal_complete" ? "没有发现待填项、事实冲突或未核验法律依据。" : "文书可以生成并下载，但提交前需要人工补充或核实标记内容。"}</p></div></div>
            <section className="legal-check-panel"><div><h2>法律依据核验</h2><p>仅发送查询词，不发送整份案件材料。未配置元典时会保留“待核验”状态。</p></div><div className="legal-check-form"><input value={legalQuery} onChange={(event) => setLegalQuery(event.target.value)} placeholder="例如：未签劳动合同二倍工资" /><button className="button button-secondary" onClick={verifyLegalBasis} disabled={!legalQuery.trim() || busy}><Search size={17} /> 核验</button></div></section>
            {record.missing_fields.length > 0 && <section className="check-section"><h2>待填信息 <span>{record.missing_fields.length}</span></h2><div className="tag-list">{record.missing_fields.map((item) => <span key={item}>{item}</span>)}</div></section>}
            {(record.unresolved_conflicts.length > 0 || record.unverified_law.length > 0) && <section className="check-section"><h2>待核实内容</h2><ul>{record.unresolved_conflicts.map((item) => <li key={item}>事实冲突：{item}</li>)}{record.unverified_law.map((item) => <li key={item}>法律依据：{item}</li>)}</ul></section>}
            <section className="output-preview"><h2>本次输出</h2>{record.case_stage === "litigation" ? <><OutputRow name="民事起诉状（要素式）" type="DOCX" /><OutputRow name="民事起诉状（普通式）" type="DOCX" /></> : <OutputRow name="劳动人事争议仲裁申请书" type="DOCX" />}<OutputRow name="证据目录（横向五列）" type="DOCX" /><OutputRow name="证据材料（连续页码）" type="PDF" muted={!record.evidence?.length} note={!record.evidence?.length ? "无证据，本次不生成" : undefined} /></section>
            {record.access_status === "locked" ? <div className="redeem-panel"><div><strong>该案件需要兑换码</strong><p>首个案件免费；后续案件需使用管理员签发的兑换码。</p></div><div className="redeem-form"><input value={redeemCode} onChange={(event) => setRedeemCode(event.target.value)} placeholder="输入兑换码" /><button className="button button-secondary" onClick={redeem} disabled={!redeemCode || busy}>解锁</button></div></div> : <button className="button button-primary button-large generate-button" onClick={generate} disabled={busy || record.generation_count >= 3}>{busy ? <><LoaderCircle className="spin" /> 正在生成文书…</> : <><Sparkles size={19} /> 生成正式稿</>}<span>{record.generation_count}/3 次已使用</span></button>}
            {!!record.artifacts?.length && <section className="downloads"><div className="downloads-heading"><h2>已生成文件</h2><span>请逐项检查后提交</span></div>{record.artifacts.map((artifact) => <a className="download-row" href={`/api/cases/${record.id}/artifacts/${artifact.id}`} key={artifact.id}><span className="file-icon"><FileText size={19} /></span><span><strong>{artifact.filename}</strong><small>点击下载</small></span><Download size={18} /></a>)}</section>}
            <div className="legal-note"><Info size={17} /><p>本工具提供文书整理辅助，不保证法院或仲裁机构受理，也不代替律师根据原件进行审查。</p></div>
          </div>}
        </section>
      </div>
    </main>
  );
}

function DetailsForm({ record, data, setData, claims, updateClaim, addClaim, removeClaim }: any) {
  return <div className="form-sections">
    <section className="form-section"><div className="form-section-title"><span>01</span><div><h2>{record.case_stage === "litigation" ? "原告" : "申请人"}</h2><p>{record.party_side === "worker" ? "劳动者一方" : "用人单位一方"}</p></div></div><div className="form-grid"><Field label="名称 / 姓名" path="parties.initiating.name" data={data} setData={setData} placeholder="请输入" /><Field label="住所地" path="parties.initiating.address" data={data} setData={setData} placeholder="请输入完整地址" /><Field label="联系方式" path="parties.initiating.contact" data={data} setData={setData} placeholder="手机号或固定电话" />{record.party_side === "worker" ? <Field label="身份证号码" path="parties.initiating.id_number" data={data} setData={setData} /> : <><Field label="统一社会信用代码" path="parties.initiating.credit_code" data={data} setData={setData} /><Field label="法定代表人" path="parties.initiating.legal_representative" data={data} setData={setData} /></>}</div></section>
    <section className="form-section"><div className="form-section-title"><span>02</span><div><h2>{record.case_stage === "litigation" ? "被告" : "被申请人"}</h2><p>{record.party_side === "worker" ? "用人单位一方" : "劳动者一方"}</p></div></div><div className="form-grid"><Field label="名称 / 姓名" path="parties.opposing.name" data={data} setData={setData} /><Field label="住所地" path="parties.opposing.address" data={data} setData={setData} /><Field label="联系方式" path="parties.opposing.contact" data={data} setData={setData} />{record.party_side === "worker" ? <><Field label="统一社会信用代码" path="parties.opposing.credit_code" data={data} setData={setData} /><Field label="法定代表人" path="parties.opposing.legal_representative" data={data} setData={setData} /></> : <Field label="身份证号码" path="parties.opposing.id_number" data={data} setData={setData} />}</div></section>
    <section className="form-section"><div className="form-section-title"><span>03</span><div><h2>劳动关系</h2><p>时间、岗位与工资</p></div></div><div className="form-grid"><Field label="入职日期" path="employment_facts.start_date" data={data} setData={setData} type="date" /><Field label="离职 / 解除日期" path="employment_facts.end_date" data={data} setData={setData} type="date" /><Field label="工作岗位" path="employment_facts.position" data={data} setData={setData} /><Field label="月工资及构成" path="employment_facts.monthly_wage" data={data} setData={setData} /></div><label className="field full"><span>基本案情</span><textarea rows={5} value={getAt(data, "employment_facts.summary")} onChange={(event) => setData(setAt(data, "employment_facts.summary", event.target.value))} placeholder="说明劳动关系、工资支付、解除经过及争议原因…" /></label></section>
    <section className="form-section"><div className="form-section-title"><span>04</span><div><h2>{record.case_stage === "litigation" ? "仲裁与诉讼" : "仲裁机构"}</h2><p>程序信息</p></div></div><div className="form-grid"><Field label="仲裁委员会" path="arbitration.committee" data={data} setData={setData} />{record.case_stage === "litigation" && <><Field label="仲裁裁决案号" path="arbitration.award_number" data={data} setData={setData} /><Field label="裁决送达日期" path="arbitration.service_date" data={data} setData={setData} type="date" /><Field label="管辖法院" path="court" data={data} setData={setData} /><Field label="履行情况" path="arbitration.payment_status" data={data} setData={setData} /></>}</div>{record.case_stage === "litigation" && <label className="field full"><span>仲裁裁决结果</span><textarea rows={4} value={getAt(data, "arbitration.result")} onChange={(event) => setData(setAt(data, "arbitration.result", event.target.value))} /></label>}</section>
    <section className="form-section"><div className="form-section-title"><span>05</span><div><h2>{record.case_stage === "litigation" ? "诉讼请求" : "仲裁请求"}</h2><p>逐项填写金额和依据</p></div></div><div className="claims-list">{claims.map((claim: any, index: number) => <div className="claim-row" key={index}><span className="claim-index">{index + 1}</span><label><span>请求类型</span><select value={claim.kind ?? "other"} onChange={(event) => updateClaim(index, "kind", event.target.value)}><option value="wages">工资</option><option value="overtime">加班费</option><option value="double_wage">未签合同二倍工资</option><option value="annual_leave">未休年休假工资</option><option value="economic_compensation">经济补偿</option><option value="illegal_termination">违法解除赔偿金</option><option value="noncompete">竞业限制</option><option value="training_liquidated_damages">培训服务期违约金</option><option value="other">其他</option></select></label><label className="claim-title"><span>请求内容</span><input value={claim.title ?? ""} onChange={(event) => updateClaim(index, "title", event.target.value)} placeholder="例如：判令被告支付拖欠工资" /></label><label><span>金额（元）</span><input type="number" value={claim.amount ?? ""} onChange={(event) => updateClaim(index, "amount", event.target.value)} /></label><label className="claim-basis"><span>计算依据</span><input value={claim.basis ?? ""} onChange={(event) => updateClaim(index, "basis", event.target.value)} placeholder="基数、期间、天数/月数或公式" /></label><button className="icon-button danger" aria-label="删除此项请求" onClick={() => removeClaim(index)}><Trash2 size={17} /></button></div>)}<button className="add-claim" onClick={addClaim}><Plus size={17} /> 添加一项请求</button></div></section>
  </div>;
}

function OutputRow({ name, type, muted, note }: { name: string; type: string; muted?: boolean; note?: string }) {
  return <div className={`output-row ${muted ? "muted" : ""}`}><span className="file-type">{type}</span><span><strong>{name}</strong>{note && <small>{note}</small>}</span>{muted ? <Info size={17} /> : <Check size={17} />}</div>;
}
