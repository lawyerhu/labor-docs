export type CaseStage = "arbitration" | "litigation";
export type PartySide = "worker" | "employer";

export interface EvidenceItem {
  id: string;
  original_name: string;
  name: string;
  purpose: string;
  size_bytes: number;
  status: string;
  processing_stage: string;
  processing_progress: number;
  analysis?: Record<string, unknown>;
}

export interface EvidenceRequirement {
  suggested_evidence: string;
  claim?: string;
  fact_to_prove?: string;
  status?: string;
}

export interface GenerationJob {
  id: string;
  status: "queued" | "dispatched" | "running" | "finalizing" | "completed" | "failed";
  stage: string;
  progress: number;
  error?: string;
}

export interface CaseStatus {
  status: string;
  analysis_pending: boolean;
  analysis_status: string;
  materials_processing: boolean;
  job: GenerationJob | null;
}

const REQUEST_TIMEOUT_MS = 30_000;

function withTimeout(init?: RequestInit): RequestInit {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  init?.signal?.addEventListener("abort", () => controller.abort(), { once: true });
  return {
    ...init,
    signal: controller.signal,
  };
}

export interface Artifact {
  id: string;
  filename: string;
  kind: string;
  created_at?: string;
  outdated?: boolean;
}

export interface WorkflowSummary {
  input_revision: number;
  confirmed_revision: number | null;
  confirmed_at: string | null;
  consent_cloud_processing: boolean;
  needs_confirmation: boolean;
}

export interface EvidenceManifestRow {
  id: string;
  name: string;
  purpose: string;
  pages: string;
  included: boolean;
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
  evidence_requirements: EvidenceRequirement[];
  generation_count: number;
  unlimited_generation: boolean;
  workflow?: WorkflowSummary;
  created_at: string;
  expires_at: string;
  evidence?: EvidenceItem[];
  artifacts?: Artifact[];
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, withTimeout({
    credentials: "include",
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
  }));
  if (!response.ok) {
    let message = "请求失败，请稍后重试";
    let body: any = null;
    try {
      body = await response.json();
      message = body.detail ?? message;
    } catch {
      message = `请求失败（HTTP ${response.status}）`;
    }
    const error = new Error(message) as Error & { status?: number; body?: any };
    error.status = response.status;
    error.body = body;
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
    request.addEventListener("timeout", () => reject(new Error("上传超时，请检查网络后重试")));
    request.timeout = 10 * 60 * 1000;
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
