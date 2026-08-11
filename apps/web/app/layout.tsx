import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "劳动文书助手",
  description: "面向劳动者与用人单位的劳动仲裁、诉讼文书和证据材料生成工具。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" data-scroll-behavior="smooth">
      <body><a className="skip-link" href="#main-content">跳到主要内容</a>{children}</body>
    </html>
  );
}
