import { Scale } from "lucide-react";
import Link from "next/link";

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <Link className="brand" href="/">
      <span className="brand-mark" aria-hidden="true"><Scale size={20} strokeWidth={1.8} /></span>
      <span>{compact ? "文书助手" : "劳动文书助手"}</span>
    </Link>
  );
}

