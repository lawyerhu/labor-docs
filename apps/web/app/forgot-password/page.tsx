"use client";

import { ArrowLeft, ArrowRight, Mail, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { Brand } from "@/components/brand";
import { api } from "@/lib/api";

type ResetCodeResponse = {
  message?: string;
  dev_code?: string;
};

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordAgain, setPasswordAgain] = useState("");
  const [code, setCode] = useState("");
  const [cooldown, setCooldown] = useState(0);
  const [sendingCode, setSendingCode] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [devCode, setDevCode] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => {
      setCooldown((current) => Math.max(0, current - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  async function sendCode() {
    const normalizedEmail = email.trim();
    if (!normalizedEmail || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      setError("请输入有效的注册邮箱地址");
      return;
    }
    setSendingCode(true);
    setError("");
    setMessage("");
    setDevCode("");
    try {
      const result = await api<ResetCodeResponse>("/api/auth/reset-code", {
        method: "POST",
        body: JSON.stringify({ email: normalizedEmail }),
      });
      setMessage(result.message || "重置验证码已发送，有效期 10 分钟");
      setDevCode(result.dev_code || "");
      setCooldown(60);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "验证码发送失败，请稍后重试");
    } finally {
      setSendingCode(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    if (password.length < 8 || password.length > 128) {
      setError("新密码长度需要为 8 至 128 位");
      return;
    }
    if (password !== passwordAgain) {
      setError("两次输入的新密码不一致");
      return;
    }
    if (!/^\d{6}$/.test(code.trim())) {
      setError("请输入 6 位邮箱验证码");
      return;
    }
    setLoading(true);
    try {
      await api("/api/auth/reset-password", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), password, code: code.trim() }),
      });
      router.push("/dashboard");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "密码重置失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-page" id="main-content">
      <div className="auth-top">
        <Brand />
        <Link href="/login" className="text-link"><ArrowLeft size={16} /> 返回登录</Link>
      </div>
      <section className="auth-panel">
        <div className="auth-copy">
          <div className="eyebrow"><span /> 密码重置</div>
          <h1>找回密码</h1>
          <p>输入注册邮箱接收验证码，验证后即可设置新密码。</p>
        </div>
        <form onSubmit={submit} className="auth-form">
          <label className="field">
            <span>注册邮箱</span>
            <div className="input-with-icon">
              <Mail size={18} />
              <input
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
                autoFocus
                placeholder="name@example.com"
                autoComplete="email"
              />
            </div>
          </label>
          <div className="code-row">
            <label className="field">
              <span>邮箱验证码</span>
              <input
                className="code-input"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                required
                autoComplete="one-time-code"
                placeholder="6 位验证码"
              />
            </label>
            <button
              className="button button-secondary"
              type="button"
              onClick={sendCode}
              disabled={sendingCode || cooldown > 0}
            >
              {sendingCode ? "发送中…" : cooldown > 0 ? `${cooldown} 秒后重发` : "获取验证码"}
            </button>
          </div>
          {message && (
            <div className="auth-message" role="status">
              {message}
              {devCode && <span>（本地验证码：<code className="inline-code">{devCode}</code>）</span>}
            </div>
          )}
          <label className="field">
            <span>设置新密码 <small>8 至 128 位</small></span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              minLength={8}
              maxLength={128}
              autoComplete="new-password"
              placeholder="请输入新密码"
            />
          </label>
          <label className="field">
            <span>确认新密码</span>
            <input
              type="password"
              value={passwordAgain}
              onChange={(event) => setPasswordAgain(event.target.value)}
              required
              minLength={8}
              maxLength={128}
              autoComplete="new-password"
              placeholder="再次输入新密码"
            />
          </label>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="button button-primary button-large full-width" disabled={loading}>
            {loading ? "正在重置密码…" : <>确认重置并登录 <ArrowRight size={18} /></>}
          </button>
        </form>
        <p className="auth-mode-actions">
          想起来了？<Link href="/login" className="text-link">返回登录</Link>
        </p>
        <p className="auth-security">
          <ShieldCheck size={16} /> 密码重置需通过邮箱验证码核验，确保仅本人可修改。
        </p>
      </section>
    </main>
  );
}
