"use client";

import { AlertCircle } from "lucide-react";
import Link from "next/link";

export default function ErrorPage({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <main className="app-shell" id="main-content">
      <section className="error-panel container">
        <div className="empty-icon"><AlertCircle size={28} /></div>
        <h1>页面出错了</h1>
        <p>加载过程中发生异常，请重试；如果问题持续，可以返回工作台重新进入。</p>
        <div className="error-actions">
          <button className="button button-primary" onClick={retry}>重试</button>
          <Link className="button button-secondary" href="/dashboard">返回工作台</Link>
        </div>
      </section>
    </main>
  );
}
