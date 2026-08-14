"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { Brand } from "@/components/brand";
import { api } from "@/lib/api";

export function AppHeader({ email }: { email?: string }) {
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
        {email && <span className="account-email">{email}</span>}
        <button className="icon-button" onClick={logout} aria-label="退出登录" title="退出登录">
          <LogOut size={18} />
        </button>
      </div>
    </header>
  );
}

