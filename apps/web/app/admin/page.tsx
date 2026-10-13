"use client";

import { ArrowLeft, ArrowRight, RefreshCw, Search, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { AppHeader } from "@/components/app-header";
import { Brand } from "@/components/brand";
import { api } from "@/lib/api";
import styles from "./admin.module.css";

interface Overview {
  admin_email: string;
  generated_at: string;
  stats: {
    total_accounts: number;
    registered_users: number;
    new_users_7d: number;
    retained_cases: number;
    accounts_with_cases: number;
    successful_generations: number;
    pending_jobs: number;
    failed_jobs: number;
  };
  users: Array<{
    id: string; email: string; created_at: string; email_verified: number; is_admin: boolean;
    case_count: number; generation_count: number; last_case_at: string | null;
  }>;
  pagination: { page: number; page_size: number; total: number };
  recent_jobs: Array<{ id: string; email: string; status: string; created_at: string; updated_at: string }>;
}

function dateTime(value: string | null) {
  return value ? new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value)) : "—";
}

const jobLabels: Record<string, string> = {
  queued: "排队中", dispatched: "已派发", running: "生成中", finalizing: "保存中", completed: "已完成", failed: "失败",
};

export default function AdminPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [error, setError] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signingIn, setSigningIn] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    setLoading(true); setError("");
    const query = new URLSearchParams({ page: String(page), search });
    api<Overview>(`/api/admin/overview?${query}`, { cache: "no-store", signal: controller.signal })
      .then((value) => { if (!cancelled) { setOverview(value); setNeedsLogin(false); } })
      .catch((reason: Error & { status?: number }) => {
        if (cancelled) return;
        setOverview(null);
        setNeedsLogin(reason.status === 401);
        if (reason.status !== 401) setError(reason.message || "统计读取失败");
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; controller.abort(); };
  }, [page, search, refresh]);

  async function login(event: FormEvent) {
    event.preventDefault(); setSigningIn(true); setError("");
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      setPassword(""); setRefresh((value) => value + 1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败");
    } finally { setSigningIn(false); }
  }

  function submitSearch(event: FormEvent) {
    event.preventDefault(); setPage(1); setSearch(searchInput.trim()); setRefresh((value) => value + 1);
  }

  const stats = overview?.stats;
  const pages = overview ? Math.max(1, Math.ceil(overview.pagination.total / overview.pagination.page_size)) : 1;

  return (
    <main className="app-shell" id="main-content">
      {overview ? <AppHeader email={overview.admin_email} isAdmin /> : (
        <header className="app-header"><Brand /><Link href="/dashboard" className="text-link"><ArrowLeft size={16} /> 返回工作台</Link></header>
      )}
      <section className={`container ${styles.page}`}>
        <div className={styles.heading}>
          <div><div className="eyebrow"><span /> 运营概览</div><h1>管理后台</h1><p>查看账号注册、案件使用与文书生成情况。</p></div>
          {!needsLogin && <button className="button button-secondary" disabled={loading} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={17} /> {loading ? "正在读取…" : "刷新数据"}</button>}
        </div>
        {error && <div className="notice notice-error" role="alert">{error}</div>}
        {needsLogin ? (
          <section className={styles.login} aria-labelledby="admin-login-title">
            <ShieldCheck size={28} /><h2 id="admin-login-title">管理员登录</h2><p>使用现有管理员邮箱和密码。普通账号无法访问运营数据。</p>
            <form className="auth-form" onSubmit={login}>
              <label className="field"><span>管理员邮箱</span><input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>
              <label className="field"><span>密码</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required maxLength={128} /></label>
              <button className="button button-primary" disabled={signingIn || loading}>{signingIn || loading ? "正在验证…" : "进入管理后台"}</button>
            </form>
          </section>
        ) : !overview ? (
          <div className="loading-panel" role="status">{loading ? "正在读取运营数据…" : "暂时无法查看数据。请使用管理员账号登录，或刷新重试。"}</div>
        ) : (
          <>
            <p className={styles.updated} role="status">更新时间：{dateTime(overview.generated_at)}（北京时间）{loading && " · 正在刷新"}</p>
            <dl className={styles.metrics}>
              {[
                ["非管理员账号", stats!.registered_users, `全部账号 ${stats!.total_accounts} 个，可能包含测试账号`],
                ["近 7 天新增账号", stats!.new_users_7d, "不含管理员账号"],
                ["有案件的账号", stats!.accounts_with_cases, "按当前保留案件统计，包含管理员"],
                ["保留案件", stats!.retained_cases, "当前数据库中的案件数"],
                ["成功生成", stats!.successful_generations, "保留案件累计成功生成次数"],
                ["进行中的任务", stats!.pending_jobs, `保留的失败任务 ${stats!.failed_jobs} 个`],
              ].map(([label, value, detail]) => <div key={label} className={styles.metric}><dt>{label}</dt><dd>{value}</dd><small>{detail}</small></div>)}
            </dl>
            <p className={styles.note}>统计说明：账号表反映当前保留的账号；案件与生成记录会随删除或 30 天到期清理而减少，不代表历史累计使用量。</p>

            <section className={styles.panel} aria-labelledby="users-title">
              <div className={styles.panelHeading}><div><h2 id="users-title">账号与使用情况</h2><p>共 {overview.pagination.total} 个匹配账号</p></div>
                <form onSubmit={submitSearch} className={styles.search}><label htmlFor="admin-search" className={styles.srOnly}>按邮箱搜索账号</label><input id="admin-search" type="search" placeholder="按邮箱搜索" value={searchInput} maxLength={254} onChange={(event) => setSearchInput(event.target.value)} /><button className="icon-button" aria-label="搜索账号" disabled={loading}><Search size={18} /></button></form>
              </div>
              <div className={styles.tableScroll} tabIndex={0} role="region" aria-label="账号使用情况表格">
                <table className={styles.table}><thead><tr><th scope="col">邮箱 / 身份</th><th scope="col">注册时间</th><th scope="col">保留案件</th><th scope="col">成功生成</th><th scope="col">最近建案</th></tr></thead>
                  <tbody>{overview.users.map((user) => <tr key={user.id}><td><strong>{user.email}</strong><small>{user.is_admin ? "管理员" : user.email_verified ? "邮箱已验证" : "邮箱未验证"}</small></td><td>{dateTime(user.created_at)}</td><td>{user.case_count}</td><td>{user.generation_count}</td><td>{dateTime(user.last_case_at)}</td></tr>)}</tbody>
                </table>
              </div>
              {overview.users.length === 0 && <p className={styles.empty}>没有匹配的账号。</p>}
              <div className={styles.pagination}><span>第 {page} 页 · 共 {pages} 页</span><div><button className="button button-secondary" disabled={loading || page <= 1} onClick={() => setPage((value) => value - 1)}><ArrowLeft size={16} /> 上一页</button><button className="button button-secondary" disabled={loading || page >= pages} onClick={() => setPage((value) => value + 1)}>下一页 <ArrowRight size={16} /></button></div></div>
            </section>

            <section className={styles.panel} aria-labelledby="jobs-title">
              <div className={styles.panelHeading}><div><h2 id="jobs-title">最近生成任务</h2><p>最近 10 个保留任务，可查看进行中或失败的任务。</p></div></div>
              <div className={styles.tableScroll} tabIndex={0} role="region" aria-label="最近生成任务表格"><table className={styles.table}><thead><tr><th scope="col">账号</th><th scope="col">状态</th><th scope="col">提交时间</th><th scope="col">更新时间</th></tr></thead><tbody>{overview.recent_jobs.map((job) => <tr key={job.id}><td>{job.email}</td><td><span className={`status-badge ${job.status === "completed" ? "complete" : "pending"}`}>{jobLabels[job.status] || job.status}</span></td><td>{dateTime(job.created_at)}</td><td>{dateTime(job.updated_at)}</td></tr>)}</tbody></table></div>
              {overview.recent_jobs.length === 0 && <p className={styles.empty}>暂无保留的生成任务。</p>}
            </section>
          </>
        )}
      </section>
    </main>
  );
}
