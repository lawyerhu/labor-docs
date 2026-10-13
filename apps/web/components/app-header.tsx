"use client";

import { LogOut, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Brand } from "@/components/brand";
import { api } from "@/lib/api";

export function AppHeader({ email, isAdmin }: { email?: string; isAdmin?: boolean }) {
  const router = useRouter();

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch {
      // 即使登出请求失败也回到登录页，避免卡在已失效会话。
    }
    router.push("/login");
    router.refresh();
  }

  return (
    <header className="app-header">
      <Brand />
      <div className="header-actions">
        {isAdmin && <Link href="/admin" className="text-link"><ShieldCheck size={16} /> 管理后台</Link>}
        {email && <span className="account-email">{email}</span>}
        <button className="icon-button" onClick={logout} aria-label="退出登录" title="退出登录">
          <LogOut size={18} />
        </button>
      </div>
    </header>
  );
}

