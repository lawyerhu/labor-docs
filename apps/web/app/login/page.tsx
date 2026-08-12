"use client";

import { ArrowLeft, ArrowRight, Mail, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Brand } from "@/components/brand";
import { api } from "@/lib/api";

type AuthMode = "login" | "register" | "otp";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codeSent, setCodeSent] = useState(false);
  const [devCode, setDevCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  function changeMode(nextMode: AuthMode) {
    setMode(nextMode);
    setCodeSent(false);
    setCode("");
    setDevCode("");
    setError("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      if (mode === "login") {
        await api("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      } else if (mode === "register") {
        if (!codeSent) {
          const result = await api<{ dev_code?: string }>("/api/auth/register-code", {
            method: "POST",
            body: JSON.stringify({ email }),
          });
          setDevCode(result.dev_code ?? "");
          setCodeSent(true);
          return;
        }
        await api("/api/auth/register", { method: "POST", body: JSON.stringify({ email, password, code }) });
      } else {
        if (!codeSent) {
          const result = await api<{ dev_code?: string }>("/api/auth/request-code", {
            method: "POST",
            body: JSON.stringify({ email }),
          });
          setDevCode(result.dev_code ?? "");
          setCodeSent(true);
          return;
        }
        await api("/api/auth/verify", { method: "POST", body: JSON.stringify({ email, code }) });
      }
      router.push("/dashboard");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  const isCodeMode = mode === "register" || mode === "otp";
  const title = mode === "login" ? "欢迎回来" : mode === "register" ? "注册账号" : "邮箱验证码登录";
  const description = mode === "login"
    ? "使用注册邮箱和密码登录。"
    : mode === "register"
      ? "邮箱验证一次，设置密码后即可登录。"
      : `验证码将发送至 ${email || "你的邮箱"}。`;

  return (
    <main className="auth-page" id="main-content">
      <div className="auth-top"><Brand /><Link href="/" className="text-link"><ArrowLeft size={16} /> 返回首页</Link></div>
      <section className="auth-panel">
        <div className="auth-copy">
          <div className="eyebrow"><span /> 安全登录</div>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        <form onSubmit={submit} className="auth-form">
          <label className="field">
            <span>邮箱（登录账号）</span>
            <div className="input-with-icon"><Mail size={18} /><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoFocus placeholder="name@example.com" autoComplete="email" /></div>
          </label>
          {mode !== "otp" && <label className="field"><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={8} maxLength={128} autoComplete={mode === "register" ? "new-password" : "current-password"} placeholder="至少 8 位" /></label>}
          {isCodeMode && codeSent && <label className="field"><span>{mode === "register" ? "注册验证码" : "验证码"}</span><input className="code-input" inputMode="numeric" maxLength={6} value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))} required autoFocus placeholder="000000" />{devCode && <small>开发环境验证码：<button type="button" className="inline-code" onClick={() => setCode(devCode)}>{devCode}</button></small>}</label>}
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="button button-primary button-large full-width" disabled={loading}>
            {loading ? "正在处理…" : mode === "login" ? <>登录并继续 <ArrowRight size={18} /></> : mode === "register" && !codeSent ? <>发送注册验证码 <ArrowRight size={18} /></> : mode === "register" ? <>完成注册 <ArrowRight size={18} /></> : !codeSent ? <>发送验证码 <ArrowRight size={18} /></> : <>登录并继续 <ArrowRight size={18} /></>}
          </button>
          {codeSent && <button className="button button-ghost full-width" type="button" onClick={() => { setCodeSent(false); setCode(""); setDevCode(""); }}>重新填写邮箱</button>}
        </form>
        <div className="auth-mode-actions">
          {mode === "login" && <><button type="button" className="text-link" onClick={() => changeMode("register")}>注册新账号</button><button type="button" className="text-link" onClick={() => changeMode("otp")}>使用旧版邮箱验证码登录</button></>}
          {mode !== "login" && <button type="button" className="text-link" onClick={() => changeMode("login")}>返回密码登录</button>}
        </div>
        <p className="auth-security"><ShieldCheck size={16} /> 注册和登录仅处理本次案件所需资料。</p>
      </section>
    </main>
  );
}
