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
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [devCode, setDevCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      if (step === "email") {
        const result = await api<{ dev_code?: string }>("/api/auth/request-code", { method: "POST", body: JSON.stringify({ email }) });
        setDevCode(result.dev_code ?? "");
        setStep("code");
      } else {
        await api("/api/auth/verify", { method: "POST", body: JSON.stringify({ email, code }) });
        router.push("/dashboard");
        router.refresh();
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page" id="main-content">
      <div className="auth-top"><Brand /><Link href="/" className="text-link"><ArrowLeft size={16} /> 返回首页</Link></div>
      <section className="auth-panel">
        <div className="auth-copy">
          <div className="eyebrow"><span /> 安全登录</div>
          <h1>{step === "email" ? "从一封邮件开始" : "输入六位验证码"}</h1>
          <p>{step === "email" ? "无需设置密码。我们会向你的邮箱发送一次性验证码。" : `验证码已发送至 ${email}`}</p>
        </div>
        <form onSubmit={submit} className="auth-form">
          {step === "email" ? (
            <label className="field"><span>邮箱地址</span><div className="input-with-icon"><Mail size={18} /><input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus placeholder="name@example.com" /></div></label>
          ) : (
            <label className="field"><span>验证码</span><input className="code-input" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required autoFocus placeholder="000000" />{devCode && <small>开发环境验证码：<button type="button" className="inline-code" onClick={() => setCode(devCode)}>{devCode}</button></small>}</label>
          )}
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="button button-primary button-large full-width" disabled={loading}>{loading ? "正在处理…" : step === "email" ? <>发送验证码 <ArrowRight size={18} /></> : <>登录并继续 <ArrowRight size={18} /></>}</button>
          {step === "code" && <button className="button button-ghost full-width" type="button" onClick={() => setStep("email")}>更换邮箱</button>}
        </form>
        <p className="auth-security"><ShieldCheck size={16} /> 登录即表示你同意仅将案件材料用于本次文书处理。</p>
      </section>
    </main>
  );
}
