"use client";

import { ArrowRight, BriefcaseBusiness, Building2, FileText, Plus, UserRound } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AppHeader } from "@/components/app-header";
import { api, CaseRecord, formatDate } from "@/lib/api";

export default function DashboardPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api<{ email: string }>("/api/auth/me"), api<CaseRecord[]>("/api/cases")])
      .then(([user, records]) => { setEmail(user.email); setCases(records); })
      .catch((reason: Error & { status?: number }) => reason.status === 401 ? router.replace("/login") : setError(reason.message))
      .finally(() => setLoading(false));
  }, [router]);

  return (
    <main className="app-shell" id="main-content">
      <AppHeader email={email} />
      <section className="dashboard container">
        <div className="dashboard-heading">
          <div><div className="eyebrow"><span /> 我的工作台</div><h1>案件</h1><p>每个案件独立保存30天。请及时下载生成文件。</p></div>
          <Link className="button button-primary" href="/cases/new"><Plus size={18} /> 创建案件</Link>
        </div>
        {error && <div className="notice notice-error">{error}</div>}
        {loading ? <div className="loading-panel">正在读取案件…</div> : cases.length === 0 ? (
          <div className="empty-state"><div className="empty-icon"><BriefcaseBusiness size={28} /></div><h2>还没有案件</h2><p>选择仲裁或起诉阶段，用几分钟建立第一份材料。</p><Link className="button button-primary" href="/cases/new">免费创建首案 <ArrowRight size={17} /></Link></div>
        ) : (
          <div className="case-list">
            {cases.map((record) => (
              <Link href={`/cases/${record.id}`} className="case-row" key={record.id}>
                <span className="case-type-icon">{record.party_side === "worker" ? <UserRound size={20} /> : <Building2 size={20} />}</span>
                <span className="case-main"><strong>{record.title}</strong><small>{record.case_stage === "arbitration" ? "劳动仲裁" : "仲裁后起诉"} · {record.party_side === "worker" ? "劳动者一方" : "用人单位一方"}</small></span>
                <span className={`status-badge ${record.readiness === "formal_complete" ? "complete" : "pending"}`}>{record.readiness === "formal_complete" ? "正式稿·信息完整" : "正式稿·含待填项"}</span>
                <span className="case-date">{formatDate(record.created_at)}</span>
                <ArrowRight className="case-arrow" size={18} />
              </Link>
            ))}
          </div>
        )}
        <aside className="dashboard-tip"><FileText size={18} /><p><strong>资料不齐也可以开始。</strong>系统会在正式稿中用醒目的“待填入”标出缺失内容，不会擅自编造。</p></aside>
      </section>
    </main>
  );
}
