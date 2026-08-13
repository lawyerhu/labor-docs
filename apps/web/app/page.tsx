import { ArrowRight, Check, FileStack, MessageSquareText, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { Brand } from "@/components/brand";

export default function Home() {
  return (
    <main className="landing" id="main-content">
      <nav className="landing-nav">
        <Brand />
        <div className="nav-actions">
          <Link className="button button-ghost" href="/login">登录</Link>
          <Link className="button button-primary button-small" href="/login">免费创建首案</Link>
        </div>
      </nav>

      <section className="hero">
        <div className="hero-copy">
          <div className="eyebrow"><span /> 劳动争议文书生成</div>
          <h1>把复杂案情，<br /><em>整理成正式文书。</em></h1>
          <p className="hero-lead">面向劳动者与用人单位。从案情梳理、金额核算到证据编排，一处完成。资料不齐也能先生成正式格式稿，缺项会明确标出。</p>
          <div className="hero-actions">
            <Link className="button button-primary button-large" href="/login">开始整理案件 <ArrowRight size={18} /></Link>
            <span className="free-note"><Check size={16} /> 首个案件完整免费</span>
          </div>
        </div>
        <div className="document-scene" aria-label="生成文书示意">
          <div className="document-paper paper-back" />
          <div className="document-paper paper-main">
            <div className="paper-rule short" />
            <h2>民事起诉状</h2>
            <div className="paper-meta">原告：某某有限公司</div>
            <div className="paper-meta">被告：曹某某</div>
            <h3>诉讼请求</h3>
            <div className="paper-line" /><div className="paper-line" /><div className="paper-line half" />
            <h3>事实与理由</h3>
            <div className="paper-line" /><div className="paper-line" /><div className="paper-line" /><div className="paper-line third" />
            <span className="paper-status"><Check size={14} /> 正式稿·信息完整</span>
          </div>
          <div className="scene-label label-one">案情与诉求分析</div>
          <div className="scene-label label-two">页码自动对应</div>
        </div>
      </section>

      <section className="trust-strip" aria-label="核心能力">
        <article><MessageSquareText /><div><strong>对话梳理</strong><span>按案件阶段逐项引导</span></div></article>
        <article><FileStack /><div><strong>证据成册</strong><span>四列 DOCX 目录、连续页码</span></div></article>
        <article><ShieldCheck /><div><strong>不编造事实</strong><span>缺项与冲突明确标记</span></div></article>
      </section>

      <section className="how-section">
        <div className="section-heading"><span>03</span><h2>三步形成可编辑材料</h2></div>
        <div className="steps-grid">
          <article><b>01</b><h3>选择案件阶段</h3><p>劳动仲裁或仲裁后起诉，劳动者和公司均可使用。</p></article>
          <article><b>02</b><h3>讲述案情、上传材料</h3><p>系统整理当事人、劳动事实、请求、仲裁经过和实际证据。</p></article>
          <article><b>03</b><h3>确认并下载正式稿</h3><p>输出可编辑 DOCX；有证据时自动生成连续编码 PDF。</p></article>
        </div>
      </section>

      <footer className="landing-footer">
        <Brand />
        <p>本工具提供文书整理辅助，不构成律师代理或案件结果保证。</p>
      </footer>
    </main>
  );
}
