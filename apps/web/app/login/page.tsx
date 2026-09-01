"use client";

import { ArrowLeft, ArrowRight, Mail, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Brand } from "@/components/brand";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      router.push("/dashboard");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登录失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page" id="main-content">
      <div className="auth-top"><Brand /><Link href="/" className="text-link"><ArrowLeft size={16} /> 返回首页</Link></div>
      <section className="auth-panel">
        <div className="auth-copy">
          <div className="eyebrow"><span /> 账号登录</div>
          <h1>登录劳动文书助手</h1>
          <p>使用注册邮箱和密码登录；还没有账号，可以先免费注册。</p>
        </div>
        <form onSubmit={submit} className="auth-form">
          <label className="field">
            <span>邮箱（登录账号）</span>
            <div className="input-with-icon"><Mail size={18} /><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoFocus placeholder="name@example.com" autoComplete="email" /></div>
          </label>
          <label className="field"><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={8} maxLength={128} autoComplete="current-password" placeholder="请输入密码" /></label>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="button button-primary button-large full-width" disabled={loading}>
            {loading ? "正在登录…" : <>登录并继续 <ArrowRight size={18} /></>}
          </button>
        </form>
        <p className="auth-mode-actions">还没有账号？<Link href="/register" className="text-link">免费注册</Link></p>
        <p className="auth-security"><ShieldCheck size={16} /> 密码经过加密存储，登录状态仅保存在当前会话中。</p>
      </section>
    </main>
  );
}
