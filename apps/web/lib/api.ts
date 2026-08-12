export type CaseStage = "arbitration" | "litigation";
export type PartySide = "worker" | "employer";

export interface EvidenceItem {
  id: string;
  original_name: string;
  name: string;
  source: string;
  purpose: string;
  size_bytes: number;
  status: string;
}

export interface Artifact {
  id: string;
  filename: string;
  kind: string;
  created_at?: string;
}

export interface CaseRecord {
  id: string;
  title: string;
  case_stage: CaseStage;
  party_side: PartySide;
  status: string;
  access_status: "free" | "locked" | "redeemed";
  data: Record<string, any>;
  readiness: "formal_with_placeholders" | "formal_complete";
  missing_fields: string[];
  unresolved_conflicts: string[];
  unverified_law: string[];
  evidence_gaps: string[];
  generation_count: number;
  unlimited_generation: boolean;
  created_at: string;
  expires_at: string;
  evidence?: EvidenceItem[];
  artifacts?: Artifact[];
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let message = "请求失败，请稍后重试";
    try {
      const body = await response.json();
      message = body.detail ?? message;
    } catch {}
    const error = new Error(message) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" }).format(new Date(value));
}

export function getAt(data: Record<string, any>, path: string): any {
  return path.split(".").reduce((current, key) => current?.[key], data) ?? "";
}

export function setAt(data: Record<string, any>, path: string, value: any): Record<string, any> {
  const result = structuredClone(data);
  const parts = path.split(".");
  let current = result;
  parts.slice(0, -1).forEach((key) => {
    current[key] = current[key] ?? {};
    current = current[key];
  });
  current[parts.at(-1)!] = value;
  return result;
}
