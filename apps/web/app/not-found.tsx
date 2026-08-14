import { FileQuestion } from "lucide-react";
import Link from "next/link";

export default function NotFoundPage() {
  return (
    <main className="app-shell" id="main-content">
      <section className="error-panel container">
        <div className="empty-icon"><FileQuestion size={28} /></div>
        <h1>页面不存在</h1>
        <p>链接可能已失效，或案件已过期被清理。</p>
        <Link className="button button-primary" href="/dashboard">返回工作台</Link>
      </section>
    </main>
  );
}