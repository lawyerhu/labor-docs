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
  processing_stage: string;
  processing_progress: number;
  analysis?: Record<string, unknown>;
}

export interface GenerationJob {
  id: string;
  status: "queued" | "dispatched" | "running" | "completed" | "failed";
  stage: string;
  progress: number;
  error?: string;
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

export function uploadEvidence(
  caseId: string,
  file: File,
  consentCloudProcessing: boolean,
  onProgress: (progress: number) => void,
): Promise<EvidenceItem> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", `/api/cases/${caseId}/evidence`);
    request.withCredentials = true;
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    });
    request.addEventListener("load", () => {
      let body: any = {};
      try { body = JSON.parse(request.responseText || "{}"); } catch {}
      if (request.status >= 200 && request.status < 300) resolve(body as EvidenceItem);
      else reject(new Error(body.detail || "证据上传失败"));
    });
    request.addEventListener("error", () => reject(new Error("网络中断，证据上传失败")));
    const form = new FormData();
    form.append("file", file);
    form.append("consent_cloud_processing", String(consentCloudProcessing));
    request.send(form);
  });
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
