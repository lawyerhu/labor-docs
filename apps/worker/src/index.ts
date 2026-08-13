interface GenerationMessage {
  type?: "generation";
  version: 1;
  job_id: string;
  case_id: string;
  requested_at: string;
}

interface EvidenceAnalysisMessage {
  type: "evidence_analysis";
  version: 1;
  evidence_id: string;
  case_id: string;
  requested_at: string;
}

type QueueMessage = GenerationMessage | EvidenceAnalysisMessage;

interface Env {
  DB: D1Database;
  FILES: R2Bucket;
  GENERATION_QUEUE: Queue<QueueMessage>;
  ENVIRONMENT: string;
  GENERATOR_URL?: string;
  GENERATOR_AUTH_TOKEN?: string;
  INTERNAL_API_TOKEN?: string;
  SESSION_SECRET?: string;
  TEST_ADMIN_ENABLED?: string;
  TEST_ADMIN_EMAIL?: string;
  TEST_ADMIN_PASSWORD?: string;
}

const SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;
const GENERATION_STALE_MS = 30 * 60 * 1000;
const EVIDENCE_ANALYSIS_TIMEOUT_MS = 3 * 60 * 1000;
const EVIDENCE_ANALYSIS_MAX_ATTEMPTS = 3;
const EVIDENCE_STALE_MS = 15 * 60 * 1000;
const CASE_ANALYSIS_TIMEOUT_MS = 20 * 1000;

type PublicUser = { id: string; email: string; unlimited_generation?: boolean };

function testAdminOnly(env: Env): boolean {
  return env.TEST_ADMIN_ENABLED?.trim().toLowerCase() === "true";
}

function isTestAdminEmail(env: Env, email: string): boolean {
  const configured = normalizeEmail(env.TEST_ADMIN_EMAIL);
  return testAdminOnly(env) && Boolean(configured && configured === email.trim().toLowerCase());
}

function isTestAdmin(env: Env, user: PublicUser): boolean {
  return isTestAdminEmail(env, user.email);
}

function bytesToHex(bytes: Uint8Array): string {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlToBytes(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function importHmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

async function hmacHex(secret: string, value: string): Promise<string> {
  const key = await importHmacKey(secret);
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(value));
  return bytesToHex(new Uint8Array(signature));
}

const PASSWORD_ITERATIONS = 120_000;

async function hashPassword(password: string): Promise<string> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const derived = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: salt.buffer as ArrayBuffer, iterations: PASSWORD_ITERATIONS, hash: "SHA-256" },
    key,
    256,
  );
  return `pbkdf2-sha256$${PASSWORD_ITERATIONS}$${bytesToBase64Url(salt)}$${bytesToBase64Url(new Uint8Array(derived))}`;
}

async function verifyPassword(password: string, encoded: string): Promise<boolean> {
  const parts = encoded.split("$");
  if (parts.length !== 4 || parts[0] !== "pbkdf2-sha256") return false;
  const iterations = Number(parts[1]);
  if (!Number.isSafeInteger(iterations) || iterations < 100_000 || iterations > 1_000_000) return false;
  try {
    const salt = base64UrlToBytes(parts[2]);
    const expected = base64UrlToBytes(parts[3]);
    const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
    const derived = new Uint8Array(await crypto.subtle.deriveBits(
      { name: "PBKDF2", salt: salt.buffer as ArrayBuffer, iterations, hash: "SHA-256" },
      key,
      expected.byteLength * 8,
    ));
    return constantTimeEqual(bytesToBase64Url(derived), bytesToBase64Url(expected));
  } catch {
    return false;
  }
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

function normalizeEmail(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) ? email : null;
}

function sessionCookie(token: string, maxAge = SESSION_MAX_AGE_SECONDS): string {
  return `labor_session=${token}; Max-Age=${maxAge}; Path=/; HttpOnly; Secure; SameSite=Lax`;
}

async function createSessionToken(secret: string, user: PublicUser): Promise<string> {
  const payload = bytesToBase64Url(new TextEncoder().encode(JSON.stringify({
    uid: user.id,
    email: user.email,
    exp: Math.floor(Date.now() / 1000) + SESSION_MAX_AGE_SECONDS,
  })));
  const key = await importHmacKey(secret);
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return `${payload}.${bytesToBase64Url(new Uint8Array(signature))}`;
}

async function readSessionToken(token: string, secret: string): Promise<PublicUser | null> {
  const [payload, encodedSignature] = token.split(".");
  if (!payload || !encodedSignature) return null;
  try {
    const decoded = JSON.parse(new TextDecoder().decode(base64UrlToBytes(payload))) as { uid?: string; email?: string; exp?: number };
    if (!decoded.uid || !decoded.email || !decoded.exp || decoded.exp <= Math.floor(Date.now() / 1000)) return null;
    const key = await importHmacKey(secret);
    const signature = new Uint8Array(base64UrlToBytes(encodedSignature)).buffer as ArrayBuffer;
    const valid = await crypto.subtle.verify(
      "HMAC",
      key,
      signature,
      new TextEncoder().encode(payload),
    );
    return valid ? { id: decoded.uid, email: decoded.email } : null;
  } catch {
    return null;
  }
}

function requestCookie(request: Request, name: string): string | null {
  const cookies = request.headers.get("Cookie") ?? "";
  const match = cookies.match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
  return match?.[1] ?? null;
}

async function currentUser(request: Request, env: Env): Promise<PublicUser | null> {
  const secret = env.SESSION_SECRET?.trim();
  const token = requestCookie(request, "labor_session");
  if (!secret || !token) return null;
  const session = await readSessionToken(token, secret);
  if (!session) return null;
  const user = await env.DB.prepare("SELECT id, email FROM users WHERE id = ?").bind(session.id).first<PublicUser>();
  return user ? { ...user, unlimited_generation: isTestAdmin(env, user) } : null;
}

type EmailSendResult = { ok: true } | { ok: false; detail: string };

async function sendOtpEmail(env: Env, email: string, code: string): Promise<EmailSendResult> {
  const generatorUrl = env.GENERATOR_URL?.trim();
  const generatorToken = env.GENERATOR_AUTH_TOKEN?.trim();
  if (!generatorUrl || !generatorToken) return { ok: false, detail: "邮件服务连接尚未配置" };
  try {
    const response = await fetch(`${generatorUrl.replace(/\/$/, "")}/internal/auth/send-otp`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${generatorToken}`,
      },
      body: JSON.stringify({ email, code }),
    });
    if (response.ok) return { ok: true };
    let detail = "验证码邮件发送失败，请稍后重试";
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.length <= 200) detail = body.detail;
    } catch {}
    if (response.status === 401) detail = "邮件服务内部令牌不匹配";
    return { ok: false, detail };
  } catch {
    return { ok: false, detail: "无法连接邮件生成服务，请稍后重试" };
  }
}

function mergeData(base: Record<string, unknown>, patch: Record<string, unknown>): Record<string, unknown> {
  const result = { ...base };
  for (const [key, value] of Object.entries(patch)) {
    result[key] = value && typeof value === "object" && !Array.isArray(value)
      && result[key] && typeof result[key] === "object" && !Array.isArray(result[key])
      ? mergeData(result[key] as Record<string, unknown>, value as Record<string, unknown>)
      : value;
  }
  return result;
}

async function requestCaseAnalysis(env: Env, payload: Record<string, unknown>): Promise<Record<string, unknown> | null> {
  const generatorUrl = env.GENERATOR_URL?.trim();
  const token = env.GENERATOR_AUTH_TOKEN?.trim();
  if (!generatorUrl || !token) return null;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), CASE_ANALYSIS_TIMEOUT_MS);
  try {
    const response = await fetch(`${generatorUrl.replace(/\/$/, "")}/internal/case-analysis`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const result = await response.json();
    return result && typeof result === "object" && !Array.isArray(result) ? result as Record<string, unknown> : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timeout);
  }
}

async function requestLegalSearch(env: Env, query: string): Promise<Record<string, unknown>> {
  const generatorUrl = env.GENERATOR_URL?.trim();
  const token = env.GENERATOR_AUTH_TOKEN?.trim();
  if (!generatorUrl || !token) throw new Error("法律核验服务尚未配置");
  const response = await fetch(`${generatorUrl.replace(/\/$/, "")}/internal/legal-search`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ query }),
  });
  if (!response.ok) {
    let detail = `法律核验服务返回 ${response.status}`;
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.length <= 200) detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  const result = await response.json();
  if (!result || typeof result !== "object" || Array.isArray(result)) throw new Error("法律核验服务返回无效结果");
  return result as Record<string, unknown>;
}

async function requestAuthCode(request: Request, env: Env): Promise<Response> {
  if (testAdminOnly(env)) return json({ detail: "当前仅开放测试管理员登录" }, { status: 503 });
  const secret = env.SESSION_SECRET?.trim();
  if (!secret) return json({ detail: "登录服务尚未配置" }, { status: 503 });
  let body: { email?: unknown };
  try {
    body = (await request.json()) as { email?: unknown };
  } catch {
    return json({ detail: "请求体必须是 JSON" }, { status: 400 });
  }
  const email = normalizeEmail(body.email);
  if (!email) return json({ detail: "请输入有效的邮箱地址" }, { status: 422 });

  const windowStart = new Date(Date.now() - 15 * 60 * 1000).toISOString();
  const recent = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM otp_codes WHERE email = ? AND created_at >= ?",
  ).bind(email, windowStart).first<{ count: number | string }>();
  if (Number(recent?.count ?? 0) >= 5) {
    return json({ detail: "验证码请求过于频繁，请 15 分钟后重试" }, { status: 429 });
  }

  const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1_000_000).padStart(6, "0");
  const now = new Date().toISOString();
  const id = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO otp_codes (id, email, code_hash, expires_at, attempts, created_at) VALUES (?, ?, ?, ?, 0, ?)",
  ).bind(id, email, await hmacHex(secret, `otp:${email}:${code}`), new Date(Date.now() + 10 * 60 * 1000).toISOString(), now).run();

  if (env.ENVIRONMENT === "local") return json({ message: "验证码已生成", dev_code: code });
  const emailResult = await sendOtpEmail(env, email, code);
  if (!emailResult.ok) {
    await env.DB.prepare("DELETE FROM otp_codes WHERE id = ?").bind(id).run();
    return json({ detail: emailResult.detail }, { status: 503 });
  }
  return json({ message: "验证码已发送，有效期 10 分钟" });
}

async function verifyAuthCode(request: Request, env: Env): Promise<Response> {
  if (testAdminOnly(env)) return json({ detail: "当前仅开放测试管理员登录" }, { status: 503 });
  const secret = env.SESSION_SECRET?.trim();
  if (!secret) return json({ detail: "登录服务尚未配置" }, { status: 503 });
  let body: { email?: unknown; code?: unknown };
  try {
    body = (await request.json()) as { email?: unknown; code?: unknown };
  } catch {
    return json({ detail: "请求体必须是 JSON" }, { status: 400 });
  }
  const email = normalizeEmail(body.email);
  const code = typeof body.code === "string" ? body.code.trim() : "";
  if (!email || !/^\d{6}$/.test(code)) return json({ detail: "邮箱或验证码格式不正确" }, { status: 400 });

  const record = await env.DB.prepare(
    "SELECT id, email, code_hash, expires_at, attempts FROM otp_codes WHERE email = ? AND (purpose = 'login' OR purpose IS NULL) AND consumed_at IS NULL ORDER BY expires_at DESC LIMIT 1",
  ).bind(email).first<{ id: string; email: string; code_hash: string; expires_at: string; attempts: number }>();
  if (!record || new Date(record.expires_at).getTime() <= Date.now()) {
    return json({ detail: "验证码无效或已过期" }, { status: 400 });
  }
  if (record.attempts >= 5) return json({ detail: "验证码尝试次数过多，请重新获取" }, { status: 429 });

  const attempts = record.attempts + 1;
  await env.DB.prepare("UPDATE otp_codes SET attempts = ? WHERE id = ?").bind(attempts, record.id).run();
  const expectedHash = await hmacHex(secret, `otp:${email}:${code}`);
  if (!constantTimeEqual(record.code_hash, expectedHash)) {
    return json({ detail: attempts >= 5 ? "验证码尝试次数过多，请重新获取" : "验证码无效或已过期" }, { status: attempts >= 5 ? 429 : 400 });
  }

  const now = new Date().toISOString();
  await env.DB.prepare("UPDATE otp_codes SET consumed_at = ? WHERE id = ?").bind(now, record.id).run();
  let user = await env.DB.prepare("SELECT id, email FROM users WHERE email = ?").bind(email).first<PublicUser>();
  if (!user) {
    const id = crypto.randomUUID();
    await env.DB.prepare("INSERT INTO users (id, email, created_at) VALUES (?, ?, ?)").bind(id, email, now).run();
    user = { id, email };
  }
  const token = await createSessionToken(secret, user);
  return json(user, { headers: { "Set-Cookie": sessionCookie(token) } });
}

type PasswordAuthBody = { email?: unknown; password?: unknown; code?: unknown };

function passwordValue(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return value.length >= 8 && value.length <= 128 ? value : null;
}

async function requestRegistrationCode(request: Request, env: Env): Promise<Response> {
  if (testAdminOnly(env)) return json({ detail: "当前仅开放测试管理员登录" }, { status: 503 });
  const secret = env.SESSION_SECRET?.trim();
  if (!secret) return json({ detail: "登录服务尚未配置" }, { status: 503 });
  let body: { email?: unknown };
  try {
    body = (await request.json()) as { email?: unknown };
  } catch {
    return json({ detail: "请求体必须是 JSON" }, { status: 400 });
  }
  const email = normalizeEmail(body.email);
  if (!email) return json({ detail: "请输入有效的邮箱地址" }, { status: 422 });

  const existing = await env.DB.prepare("SELECT password_hash FROM users WHERE email = ?").bind(email).first<{ password_hash: string | null }>();
  if (existing?.password_hash) return json({ detail: "该邮箱已注册，请直接使用密码登录" }, { status: 409 });

  const windowStart = new Date(Date.now() - 15 * 60 * 1000).toISOString();
  const recent = await env.DB.prepare(
    "SELECT COUNT(*) AS count FROM otp_codes WHERE email = ? AND purpose = 'register' AND created_at >= ?",
  ).bind(email, windowStart).first<{ count: number | string }>();
  if (Number(recent?.count ?? 0) >= 5) {
    return json({ detail: "验证码请求过于频繁，请 15 分钟后重试" }, { status: 429 });
  }

  const code = String(crypto.getRandomValues(new Uint32Array(1))[0] % 1_000_000).padStart(6, "0");
  const now = new Date().toISOString();
  const id = crypto.randomUUID();
  await env.DB.prepare(
    "INSERT INTO otp_codes (id, email, code_hash, expires_at, attempts, purpose, created_at) VALUES (?, ?, ?, ?, 0, 'register', ?)",
  ).bind(id, email, await hmacHex(secret, `otp:register:${email}:${code}`), new Date(Date.now() + 10 * 60 * 1000).toISOString(), now).run();

  if (env.ENVIRONMENT === "local") return json({ message: "注册验证码已生成", dev_code: code });
  const emailResult = await sendOtpEmail(env, email, code);
  if (!emailResult.ok) {
    await env.DB.prepare("DELETE FROM otp_codes WHERE id = ?").bind(id).run();
    return json({ detail: emailResult.detail }, { status: 503 });
  }
  return json({ message: "注册验证码已发送，有效期 10 分钟" });
}

async function registerPassword(request: Request, env: Env): Promise<Response> {
  if (testAdminOnly(env)) return json({ detail: "当前仅开放测试管理员登录" }, { status: 503 });
  const secret = env.SESSION_SECRET?.trim();
  if (!secret) return json({ detail: "登录服务尚未配置" }, { status: 503 });
  let body: PasswordAuthBody;
  try {
    body = (await request.json()) as PasswordAuthBody;
  } catch {
    return json({ detail: "请求体必须是 JSON" }, { status: 400 });
  }
  const email = normalizeEmail(body.email);
  const password = passwordValue(body.password);
  const code = typeof body.code === "string" ? body.code.trim() : "";
  if (!email || !password || !/^\d{6}$/.test(code)) {
    return json({ detail: "邮箱、密码或验证码格式不正确" }, { status: 400 });
  }

  const record = await env.DB.prepare(
    "SELECT id, code_hash, expires_at, attempts FROM otp_codes WHERE email = ? AND purpose = 'register' AND consumed_at IS NULL ORDER BY created_at DESC LIMIT 1",
  ).bind(email).first<{ id: string; code_hash: string; expires_at: string; attempts: number }>();
  if (!record || new Date(record.expires_at).getTime() <= Date.now()) {
    return json({ detail: "验证码无效或已过期" }, { status: 400 });
  }
  if (record.attempts >= 5) return json({ detail: "验证码尝试次数过多，请重新获取" }, { status: 429 });

  const attempts = record.attempts + 1;
  await env.DB.prepare("UPDATE otp_codes SET attempts = ? WHERE id = ?").bind(attempts, record.id).run();
  const expectedHash = await hmacHex(secret, `otp:register:${email}:${code}`);
  if (!constantTimeEqual(record.code_hash, expectedHash)) {
    return json({ detail: attempts >= 5 ? "验证码尝试次数过多，请重新获取" : "验证码无效或已过期" }, { status: attempts >= 5 ? 429 : 400 });
  }

  const now = new Date().toISOString();
  const passwordHash = await hashPassword(password);
  await env.DB.prepare("UPDATE otp_codes SET consumed_at = ? WHERE id = ?").bind(now, record.id).run();
  const existing = await env.DB.prepare("SELECT id, email, password_hash FROM users WHERE email = ?").bind(email).first<{ id: string; email: string; password_hash: string | null }>();
  let user: PublicUser;
  if (existing) {
    if (existing.password_hash) return json({ detail: "该邮箱已注册，请直接使用密码登录" }, { status: 409 });
    await env.DB.prepare("UPDATE users SET password_hash = ?, email_verified = 1 WHERE id = ?").bind(passwordHash, existing.id).run();
    user = { id: existing.id, email: existing.email };
  } else {
    const id = crypto.randomUUID();
    await env.DB.prepare("INSERT INTO users (id, email, password_hash, email_verified, created_at) VALUES (?, ?, ?, 1, ?)").bind(id, email, passwordHash, now).run();
    user = { id, email };
  }
  const token = await createSessionToken(secret, user);
  return json(user, { headers: { "Set-Cookie": sessionCookie(token) } });
}

async function loginPassword(request: Request, env: Env): Promise<Response> {
  const secret = env.SESSION_SECRET?.trim();
  if (!secret) return json({ detail: "登录服务尚未配置" }, { status: 503 });
  let body: PasswordAuthBody;
  try {
    body = (await request.json()) as PasswordAuthBody;
  } catch {
    return json({ detail: "请求体必须是 JSON" }, { status: 400 });
  }
  const email = normalizeEmail(body.email);
  const password = typeof body.password === "string" ? body.password : "";
  if (!email || password.length < 1 || password.length > 128) return json({ detail: "邮箱或密码不正确" }, { status: 401 });

  const adminEmail = normalizeEmail(env.TEST_ADMIN_EMAIL);
  const adminPassword = env.TEST_ADMIN_PASSWORD ?? "";
  if (testAdminOnly(env) && adminEmail && adminPassword && email === adminEmail && constantTimeEqual(password, adminPassword)) {
    const now = new Date().toISOString();
    let user = await env.DB.prepare("SELECT id, email FROM users WHERE email = ?").bind(email).first<PublicUser>();
    if (!user) {
      const id = crypto.randomUUID();
      await env.DB.prepare("INSERT INTO users (id, email, created_at) VALUES (?, ?, ?)").bind(id, email, now).run();
      user = { id, email };
    }
    const publicUser = { ...user, unlimited_generation: true };
    const token = await createSessionToken(secret, publicUser);
    return json(publicUser, { headers: { "Set-Cookie": sessionCookie(token) } });
  }

  if (testAdminOnly(env)) return json({ detail: "邮箱或密码不正确" }, { status: 401 });

  const user = await env.DB.prepare("SELECT id, email, password_hash, email_verified FROM users WHERE email = ?").bind(email).first<{ id: string; email: string; password_hash: string | null; email_verified: number }>();
  if (!user?.password_hash || user.email_verified !== 1 || !(await verifyPassword(password, user.password_hash))) {
    return json({ detail: "邮箱或密码不正确" }, { status: 401 });
  }
  const publicUser = { id: user.id, email: user.email };
  const token = await createSessionToken(secret, publicUser);
  return json({ ...publicUser, unlimited_generation: false }, { headers: { "Set-Cookie": sessionCookie(token) } });
}

function json(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...init.headers,
    },
  });
}

type CaseRow = {
  id: string;
  user_id: string;
  title: string;
  case_stage: string;
  party_side: string;
  status: string;
  access_status: string;
  data_json: string;
  generation_count: number;
  generation_version: string;
  created_at: string;
  expires_at: string;
};

type EvidenceRow = {
  id: string;
  original_name: string;
  name: string;
  purpose: string;
  object_key: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  status: string;
  processing_stage: string;
  processing_progress: number;
  analysis_json: string;
  created_at: string;
};

function parseCaseData(value: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(value || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function hasValue(value: unknown): boolean {
  return typeof value === "string" ? value.trim().length > 0 : value !== null && value !== undefined;
}

function valueAt(data: Record<string, unknown>, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => {
    return current && typeof current === "object" ? (current as Record<string, unknown>)[key] : undefined;
  }, data);
}

function readinessFor(row: CaseRow, data: Record<string, unknown>) {
  const missing: string[] = [];
  const required = [
    ["employment_facts.start_date", "入职日期"],
    ["employment_facts.summary", "基本案情"],
  ] as const;
  for (const [path, label] of required) if (!hasValue(valueAt(data, path))) missing.push(label);
  if (row.case_stage === "litigation") {
    if (!hasValue(valueAt(data, "arbitration.service_date"))) missing.push("仲裁裁决送达日期");
    if (!hasValue(valueAt(data, "court"))) missing.push("管辖法院");
  }
  const conflicts = Array.isArray(data.unresolved_conflicts)
    ? data.unresolved_conflicts.filter((item): item is string => typeof item === "string")
    : [];
  const unverifiedLaw = Array.isArray(data.unverified_law)
    ? data.unverified_law.filter((item): item is string => typeof item === "string")
    : [];
  const evidenceGaps = Array.isArray(data.evidence_gaps)
    ? data.evidence_gaps.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
  return {
    readiness: missing.length || conflicts.length || unverifiedLaw.length
      ? "formal_with_placeholders"
      : "formal_complete",
    missing_fields: missing,
    unresolved_conflicts: conflicts,
    unverified_law: unverifiedLaw,
    evidence_gaps: [...new Set(evidenceGaps)].slice(0, 20),
  };
}

function evidencePayload(item: EvidenceRow) {
  return {
    id: item.id,
    original_name: item.original_name,
    name: item.name,
    purpose: item.purpose,
    mime_type: item.mime_type,
    size_bytes: item.size_bytes,
    sha256: item.sha256,
    status: item.status,
    processing_stage: item.processing_stage,
    processing_progress: item.processing_progress,
    analysis: parseCaseData(item.analysis_json),
    created_at: item.created_at,
  };
}

function fallbackCaseAnalysis(row: CaseRow, data: Record<string, unknown>, round: number, supplement: string): Record<string, unknown> {
  const intake = data.intake && typeof data.intake === "object" ? data.intake as Record<string, unknown> : {};
  const facts = String(intake.facts || "").trim();
  const claims = String(intake.claims_text || "").trim();
  const requirements = [
    { suggested_evidence: "劳动合同或入职材料", status: "not_submitted" },
    { suggested_evidence: "工资及考勤记录", status: "not_submitted" },
    { suggested_evidence: "解除、离职或协商材料", status: "not_submitted" },
    { suggested_evidence: "仲裁裁决书及送达凭证", status: "not_submitted" },
  ];
  return {
    case_stage: row.case_stage,
    party_side: row.party_side,
    summary: facts,
    claims_summary: claims,
    legal_analysis: "外部分析服务暂时不可用，已保存基础追问和证据建议；可以稍后点击“重新分析”。",
    follow_up_questions: round === 1
      ? [
        "请补充入职、离职或解除劳动关系的关键日期、岗位及工资构成。",
        "请说明每项请求的金额、计算期间、计算基数以及仲裁已经支持或驳回的部分。",
        "仲裁裁决何时送达？劳动合同履行地或用人单位所在地在哪个区县？",
        "目前有哪些劳动合同、工资记录、考勤、聊天记录、解除通知或仲裁材料可以上传？",
      ]
      : [],
    evidence_suggestions: requirements.map((item) => item.suggested_evidence),
    data_patch: {
      employment_facts: { summary: [facts, supplement].filter(Boolean).join("\n") },
      claims: [{ kind: "other", title: claims || "[待分析诉求]", basis: "[待填入：金额、计算基数、期间或计算方式]" }],
      evidence_gaps: requirements.map((item) => item.suggested_evidence),
      evidence_requirements: requirements,
    },
    analysis_status: "fallback",
  };
}

async function ownedCase(request: Request, env: Env, caseId: string): Promise<{ user: PublicUser; row: CaseRow } | Response> {
  const user = await currentUser(request, env);
  if (!user) return json({ detail: "请先登录" }, { status: 401 });
  const row = await env.DB.prepare(
    "SELECT id, user_id, title, case_stage, party_side, status, access_status, data_json, generation_count, generation_version, created_at, expires_at FROM cases WHERE id = ? AND user_id = ?",
  ).bind(caseId, user.id).first<CaseRow>();
  if (!row) return json({ detail: "案件不存在" }, { status: 404 });
  if (new Date(row.expires_at).getTime() <= Date.now()) return json({ detail: "案件已经到期" }, { status: 410 });
  return { user, row };
}

async function casePayload(env: Env, row: CaseRow, user?: PublicUser) {
  await recoverStaleEvidence(env, row.id);
  const data = parseCaseData(row.data_json);
  const readiness = readinessFor(row, data);
  const [evidence, artifacts] = await Promise.all([
    env.DB.prepare(
      "SELECT id, original_name, name, purpose, object_key, mime_type, size_bytes, sha256, status, processing_stage, processing_progress, analysis_json, created_at FROM evidence WHERE case_id = ? ORDER BY created_at, id",
    ).bind(row.id).all<EvidenceRow>(),
    env.DB.prepare(
      "SELECT id, filename, kind, created_at FROM artifacts WHERE case_id = ? ORDER BY created_at, id",
    ).bind(row.id).all<{ id: string; filename: string; kind: string; created_at: string }>(),
  ]);
  return {
    id: row.id,
    title: row.title,
    case_stage: row.case_stage,
    party_side: row.party_side,
    status: row.status,
    access_status: row.access_status,
    data,
    generation_count: row.generation_count,
    unlimited_generation: user ? isTestAdmin(env, user) : false,
    generation_version: row.generation_version,
    created_at: row.created_at,
    expires_at: row.expires_at,
    ...readiness,
    evidence_requirements: Array.isArray(data.evidence_requirements) ? data.evidence_requirements : [],
    evidence: evidence.results.map(evidencePayload),
    artifacts: artifacts.results,
  };
}

async function recoverStaleEvidence(env: Env, caseId: string): Promise<void> {
  const staleBefore = new Date(Date.now() - EVIDENCE_STALE_MS).toISOString();
  const error = JSON.stringify({ error: "材料识别超过15分钟未完成，请删除该材料后重新上传。" });
  const stale = await env.DB.prepare("SELECT id FROM evidence WHERE case_id = ? AND status IN ('queued', 'processing') AND created_at < ? LIMIT 1")
    .bind(caseId, staleBefore).first<{ id: string }>();
  if (!stale) return;
  await env.DB.batch([
    env.DB.prepare("UPDATE evidence SET status = 'failed', processing_stage = 'failed', processing_progress = 100, analysis_json = ? WHERE case_id = ? AND status IN ('queued', 'processing') AND created_at < ?")
      .bind(error, caseId, staleBefore),
    env.DB.prepare("UPDATE cases SET status = 'ready_to_generate' WHERE id = ? AND NOT EXISTS (SELECT 1 FROM evidence WHERE case_id = ? AND status IN ('queued', 'processing'))")
      .bind(caseId, caseId),
  ]);
}

async function listCases(request: Request, env: Env): Promise<Response> {
  const user = await currentUser(request, env);
  if (!user) return json({ detail: "请先登录" }, { status: 401 });
  const rows = await env.DB.prepare(
    "SELECT id, user_id, title, case_stage, party_side, status, access_status, data_json, generation_count, generation_version, created_at, expires_at FROM cases WHERE user_id = ? ORDER BY created_at DESC",
  ).bind(user.id).all<CaseRow>();
  return json(await Promise.all(rows.results.map((row) => casePayload(env, row, user))));
}

async function createCase(request: Request, env: Env): Promise<Response> {
  const user = await currentUser(request, env);
  if (!user) return json({ detail: "请先登录" }, { status: 401 });
  let body: { title?: unknown; case_stage?: unknown; party_side?: unknown; facts?: unknown; claims_text?: unknown };
  try {
    body = await request.json() as typeof body;
  } catch {
    return json({ detail: "请求体必须是 JSON" }, { status: 400 });
  }
  const facts = typeof body.facts === "string" ? body.facts.trim().slice(0, 20000) : "";
  const claimsText = typeof body.claims_text === "string" ? body.claims_text.trim().slice(0, 10000) : "";
  if (body.case_stage !== "arbitration" && body.case_stage !== "litigation") return json({ detail: "请选择当前案件阶段" }, { status: 422 });
  if (body.party_side !== "worker" && body.party_side !== "employer") return json({ detail: "请选择申请人/原告一方" }, { status: 422 });
  const stage = body.case_stage;
  const side = body.party_side;
  const title = typeof body.title === "string" && body.title.trim() ? body.title.trim().slice(0, 200) : "劳动争议案件";

  const entitlement = await env.DB.prepare("SELECT free_case_used FROM users WHERE id = ?").bind(user.id).first<{ free_case_used: number }>();
  const accessStatus = Number(entitlement?.free_case_used ?? 0) === 0 || isTestAdmin(env, user) ? "free" : "locked";
  const now = new Date().toISOString();
  const row: CaseRow = {
    id: crypto.randomUUID(), user_id: user.id, title, case_stage: stage, party_side: side,
    status: "draft", access_status: accessStatus, data_json: JSON.stringify({
      intake: { facts, claims_text: claimsText },
      conversation: [{ role: "user", content: `案情：${facts}\n诉求：${claimsText}` }],
    }),
    generation_count: 0, generation_version: "rules-1.0/templates-1.0", created_at: now,
    expires_at: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString(),
  };
  await env.DB.batch([
    env.DB.prepare(
      "INSERT INTO cases (id, user_id, title, case_stage, party_side, status, access_status, data_json, generation_count, generation_version, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    ).bind(row.id, row.user_id, row.title, row.case_stage, row.party_side, row.status, row.access_status, row.data_json, row.generation_count, row.generation_version, row.created_at, row.expires_at),
    env.DB.prepare("UPDATE users SET free_case_used = 1 WHERE id = ?").bind(user.id),
  ]);
  return json(await casePayload(env, row, user), { status: 201 });
}

async function updateCase(request: Request, env: Env, caseId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  let body: { title?: unknown; data?: unknown };
  try {
    body = await request.json() as typeof body;
  } catch {
    return json({ detail: "请求体必须是 JSON" }, { status: 400 });
  }
  const title = typeof body.title === "string" ? body.title.trim().slice(0, 200) : owned.row.title;
  const data = body.data && typeof body.data === "object" && !Array.isArray(body.data) ? body.data : parseCaseData(owned.row.data_json);
  await env.DB.prepare("UPDATE cases SET title = ?, data_json = ?, status = 'ready_to_generate' WHERE id = ? AND user_id = ?")
    .bind(title, JSON.stringify(data), caseId, owned.user.id).run();
  const row = { ...owned.row, title, data_json: JSON.stringify(data), status: "ready_to_generate" };
  return json(await casePayload(env, row, owned.user));
}

async function chatCase(request: Request, env: Env, caseId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  let body: { message?: unknown; consent_cloud_processing?: unknown };
  try { body = await request.json() as typeof body; } catch { return json({ detail: "请求体必须是 JSON" }, { status: 400 }); }
  const message = typeof body.message === "string" ? body.message.trim().slice(0, 20000) : "";
  if (!message) return json({ detail: "请输入补充内容" }, { status: 422 });
  if (body.consent_cloud_processing !== true) return json({ detail: "请先同意将案情文本发送至大模型和元典进行本次分析" }, { status: 422 });
  const data = parseCaseData(owned.row.data_json);
  const intake = data.intake && typeof data.intake === "object" ? data.intake as Record<string, unknown> : {};
  const priorAnalysis = data.analysis && typeof data.analysis === "object" ? data.analysis as Record<string, unknown> : {};
  if (priorAnalysis.round === 2) return json({ detail: "案件分析已经完成，可以直接上传证据并生成材料" }, { status: 409 });
  const round = priorAnalysis.round === 1 ? 2 : 1;
  const conversation = Array.isArray(data.conversation) ? [...data.conversation] : [];
  if (round === 2) conversation.push({ role: "user", content: message });
  const analysis = await requestCaseAnalysis(env, {
    facts: String(intake.facts || message),
    claims_text: String(intake.claims_text || "[待分析诉求]"),
    supplement: round === 2 ? message : "",
    current_data: data,
    round,
  }) ?? fallbackCaseAnalysis(owned.row, data, round, round === 2 ? message : "");
  const patch = analysis.data_patch && typeof analysis.data_patch === "object" && !Array.isArray(analysis.data_patch)
    ? analysis.data_patch as Record<string, unknown> : {};
  const merged = mergeData(data, patch);
  const questions = Array.isArray(analysis.follow_up_questions) ? analysis.follow_up_questions.map(String) : [];
  const reply = round === 1 && questions.length
    ? `分析完成。请一次性补充以下信息：\n${questions.map((item, index) => `${index + 1}. ${item}`).join("\n")}`
    : "补充信息已合并。现在可以上传现有证据并直接生成文书。";
  conversation.push({ role: "assistant", content: reply });
  merged.conversation = conversation;
  // The user-selected routing fields are authoritative. The analysis model may
  // describe a conflicting stage or side, but must never reroute the case.
  const stage = owned.row.case_stage;
  const side = owned.row.party_side;
  merged.analysis = { ...analysis, case_stage: stage, party_side: side, round };
  await env.DB.prepare("UPDATE cases SET data_json = ?, case_stage = ?, party_side = ?, status = ? WHERE id = ? AND user_id = ?")
    .bind(JSON.stringify(merged), stage, side, round === 1 ? "pending_confirmation" : "ready_to_generate", caseId, owned.user.id).run();
  const updatedRow = { ...owned.row, data_json: JSON.stringify(merged), case_stage: stage, party_side: side };
  const assessment = readinessFor(updatedRow, merged);
  return json({ data: merged, reply, readiness: assessment.readiness, missing_fields: assessment.missing_fields });
}

function safeFilename(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]+/g, "_").slice(0, 120) || "file";
}

async function sha256Hex(value: ArrayBuffer): Promise<string> {
  return bytesToHex(new Uint8Array(await crypto.subtle.digest("SHA-256", value)));
}

async function uploadEvidence(request: Request, env: Env, caseId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  const form = await request.formData();
  if (form.get("consent_cloud_processing") !== "true") {
    return json({ detail: "请先同意将案情说明和材料文字发送至大模型进行本次处理" }, { status: 422 });
  }
  const value = form.get("file");
  if (!(value instanceof File) || !value.name) return json({ detail: "请选择证据文件" }, { status: 422 });
  const count = await env.DB.prepare("SELECT COUNT(*) AS count FROM evidence WHERE case_id = ?").bind(caseId).first<{ count: number | string }>();
  if (Number(count?.count ?? 0) >= 20) return json({ detail: "每个案件最多上传20个文件" }, { status: 413 });
  if (value.size > 50 * 1024 * 1024) return json({ detail: "单个文件不能超过50MB" }, { status: 413 });
  const allowed = /\.(pdf|jpg|jpeg|png|doc|docx|xls|xlsx)$/i.test(value.name);
  if (!allowed) return json({ detail: "不支持的文件类型" }, { status: 415 });
  const bytes = await value.arrayBuffer();
  const id = crypto.randomUUID();
  const key = `cases/${owned.user.id}/${caseId}/${id}-${safeFilename(value.name)}`;
  await env.FILES.put(key, bytes, { httpMetadata: { contentType: value.type || "application/octet-stream" } });
  const now = new Date().toISOString();
  const row: EvidenceRow = {
    id, original_name: value.name.slice(0, 255), name: "材料识别中",
    purpose: "", object_key: key, mime_type: value.type || "application/octet-stream",
    size_bytes: value.size, sha256: await sha256Hex(bytes), status: "processing",
    processing_stage: "queued", processing_progress: 10, analysis_json: "{}", created_at: now,
  };
  await env.DB.batch([
    env.DB.prepare("INSERT INTO evidence (id, case_id, original_name, name, purpose, object_key, mime_type, size_bytes, sha256, status, processing_stage, processing_progress, analysis_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
      .bind(row.id, caseId, row.original_name, row.name, row.purpose, row.object_key, row.mime_type, row.size_bytes, row.sha256, row.status, row.processing_stage, row.processing_progress, row.analysis_json, row.created_at),
    env.DB.prepare("UPDATE cases SET status = 'materials_processing' WHERE id = ?").bind(caseId),
  ]);
  try {
    await env.GENERATION_QUEUE.send({ type: "evidence_analysis", version: 1, evidence_id: id, case_id: caseId, requested_at: now });
  } catch (error) {
    await env.DB.prepare("UPDATE evidence SET status = 'failed', processing_stage = 'failed', processing_progress = 100 WHERE id = ?")
      .bind(id).run();
    return json({ detail: `材料识别任务入队失败：${String(error)}` }, { status: 503 });
  }
  return json(evidencePayload(row), { status: 201 });
}

async function updateEvidence(request: Request, env: Env, caseId: string, evidenceId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  let body: { name?: unknown; purpose?: unknown };
  try { body = await request.json() as typeof body; } catch { return json({ detail: "请求体必须是 JSON" }, { status: 400 }); }
  const row = await env.DB.prepare("SELECT id, original_name, name, purpose, object_key, mime_type, size_bytes, sha256, status, processing_stage, processing_progress, analysis_json, created_at FROM evidence WHERE id = ? AND case_id = ?")
    .bind(evidenceId, caseId).first<EvidenceRow>();
  if (!row) return json({ detail: "证据不存在" }, { status: 404 });
  const next = {
    name: typeof body.name === "string" ? body.name.trim().slice(0, 255) : row.name,
    purpose: typeof body.purpose === "string" ? body.purpose.trim().slice(0, 2000) : row.purpose,
  };
  await env.DB.prepare("UPDATE evidence SET name = ?, purpose = ? WHERE id = ? AND case_id = ?")
    .bind(next.name, next.purpose, evidenceId, caseId).run();
  return json({ ...row, ...next });
}

async function deleteEvidence(request: Request, env: Env, caseId: string, evidenceId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  const row = await env.DB.prepare("SELECT object_key FROM evidence WHERE id = ? AND case_id = ?").bind(evidenceId, caseId).first<{ object_key: string }>();
  if (!row) return json({ detail: "证据不存在" }, { status: 404 });
  await env.FILES.delete(row.object_key);
  await env.DB.prepare("DELETE FROM evidence WHERE id = ? AND case_id = ?").bind(evidenceId, caseId).run();
  return new Response(null, { status: 204 });
}

async function deleteCase(request: Request, env: Env, caseId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  const running = await env.DB.prepare(
    "SELECT id FROM generation_jobs WHERE case_id = ? AND status IN ('queued', 'dispatched', 'running', 'finalizing') LIMIT 1",
  ).bind(caseId).first<{ id: string }>();
  if (running) return json({ detail: "案件正在生成，请等待任务结束后再删除" }, { status: 409 });

  const objects = await env.DB.prepare(
    "SELECT object_key FROM evidence WHERE case_id = ? UNION ALL SELECT object_key FROM artifacts WHERE case_id = ?",
  ).bind(caseId, caseId).all<{ object_key: string }>();
  try {
    await Promise.all([...new Set(objects.results.map((item) => item.object_key).filter(Boolean))].map((key) => env.FILES.delete(key)));
  } catch {
    return json({ detail: "案件文件清理失败，请稍后重试" }, { status: 503 });
  }

  await env.DB.batch([
    env.DB.prepare("UPDATE redemption_codes SET redeemed_case_id = NULL WHERE redeemed_case_id = ?").bind(caseId),
    env.DB.prepare("DELETE FROM generation_jobs WHERE case_id = ?").bind(caseId),
    env.DB.prepare("DELETE FROM notifications WHERE case_id = ?").bind(caseId),
    env.DB.prepare("DELETE FROM evidence WHERE case_id = ?").bind(caseId),
    env.DB.prepare("DELETE FROM artifacts WHERE case_id = ?").bind(caseId),
    env.DB.prepare("DELETE FROM cases WHERE id = ? AND user_id = ?").bind(caseId, owned.user.id),
  ]);
  return new Response(null, { status: 204 });
}

async function markGenerationJobFailed(env: Env, jobId: string, caseId: string | null, error: string): Promise<void> {
  const now = new Date().toISOString();
  const statements = [
    env.DB.prepare(
      "UPDATE generation_jobs SET status = 'failed', stage = 'failed', progress = 100, error = ?, updated_at = ? WHERE id = ? AND status != 'completed'",
    ).bind(error.slice(0, 2000), now, jobId),
  ];
  if (caseId) {
    statements.push(
      env.DB.prepare(
        "UPDATE cases SET status = 'ready_to_generate' WHERE id = ? AND NOT EXISTS (SELECT 1 FROM generation_jobs WHERE case_id = ? AND id != ? AND status IN ('queued', 'dispatched', 'running', 'finalizing'))",
      ).bind(caseId, caseId, jobId),
    );
  }
  await env.DB.batch(statements);
}

async function cacheEvidenceAnalysis(request: Request, env: Env, caseId: string, evidenceId: string): Promise<Response> {
  if (!isAuthorized(request, env)) return json({ detail: "未授权" }, { status: 401 });
  let body: { analysis?: unknown };
  try { body = await request.json() as typeof body; } catch { return json({ detail: "请求体必须是 JSON" }, { status: 400 }); }
  if (!body.analysis || typeof body.analysis !== "object" || Array.isArray(body.analysis)) {
    return json({ detail: "分析缓存无效" }, { status: 400 });
  }
  const existing = await env.DB.prepare("SELECT analysis_json FROM evidence WHERE id = ? AND case_id = ?")
    .bind(evidenceId, caseId)
    .first<{ analysis_json: string }>();
  if (!existing) return json({ detail: "证据不存在" }, { status: 404 });
  const merged = { ...parseCaseData(existing.analysis_json), ...(body.analysis as Record<string, unknown>) };
  await env.DB.prepare("UPDATE evidence SET analysis_json = ? WHERE id = ? AND case_id = ?")
    .bind(JSON.stringify(merged), evidenceId, caseId)
    .run();
  return json({ status: "ok" });
}

async function failStaleGenerationJobs(env: Env, caseId: string): Promise<void> {
  const staleBefore = new Date(Date.now() - GENERATION_STALE_MS).toISOString();
  const now = new Date().toISOString();
  await env.DB.batch([
    env.DB.prepare(
      "UPDATE generation_jobs SET status = 'failed', stage = 'failed', progress = 100, error = '生成任务超过30分钟未更新，已自动结束，请重新生成', updated_at = ? WHERE case_id = ? AND status IN ('queued', 'dispatched', 'running', 'finalizing') AND updated_at < ?",
    ).bind(now, caseId, staleBefore),
    env.DB.prepare(
      "UPDATE cases SET status = 'ready_to_generate' WHERE id = ? AND NOT EXISTS (SELECT 1 FROM generation_jobs WHERE case_id = ? AND status IN ('queued', 'dispatched', 'running', 'finalizing'))",
    ).bind(caseId, caseId),
  ]);
}

async function generateCase(request: Request, env: Env, caseId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  await recoverStaleEvidence(env, caseId);
  let body: { consent_cloud_processing?: unknown };
  try { body = await request.json() as typeof body; } catch { return json({ detail: "请求体必须是 JSON" }, { status: 400 }); }
  if (body.consent_cloud_processing !== true) {
    return json({ detail: "请先同意将案情说明和材料文字发送至大模型进行本次处理" }, { status: 422 });
  }
  if (isTestAdmin(env, owned.user)) {
    owned.row.access_status = "free";
    owned.row.generation_count = 0;
  }
  if (owned.row.access_status === "locked") return json({ detail: "请先使用兑换码解锁该案件" }, { status: 402 });
  if (owned.row.generation_count >= 3) return json({ detail: "本案件最多成功生成3次" }, { status: 429 });
  const material = await env.DB.prepare("SELECT id FROM evidence WHERE case_id = ? AND status IN ('queued', 'processing') LIMIT 1")
    .bind(caseId).first<{ id: string }>();
  if (material) return json({ detail: "材料仍在识别，请等待进度完成后再生成" }, { status: 409 });
  await failStaleGenerationJobs(env, caseId);
  const running = await env.DB.prepare("SELECT id FROM generation_jobs WHERE case_id = ? AND status IN ('queued', 'running', 'dispatched', 'finalizing') LIMIT 1")
    .bind(caseId).first<{ id: string }>();
  if (running) return json({ detail: "已有生成任务正在处理中" }, { status: 409 });
  const jobId = crypto.randomUUID();
  const now = new Date().toISOString();
  await env.DB.batch([
    env.DB.prepare("INSERT INTO generation_jobs (id, case_id, status, stage, progress, created_at, updated_at) VALUES (?, ?, 'queued', 'queued', 5, ?, ?)").bind(jobId, caseId, now, now),
    env.DB.prepare("UPDATE cases SET status = 'generating' WHERE id = ?").bind(caseId),
  ]);
  try {
    await env.GENERATION_QUEUE.send({ version: 1, job_id: jobId, case_id: caseId, requested_at: now });
  } catch (error) {
    await markGenerationJobFailed(env, jobId, caseId, `入队失败：${String(error)}`);
    return json({ detail: "生成任务入队失败", job_id: jobId }, { status: 503 });
  }
  return json({ job_id: jobId, status: "queued" }, { status: 202 });
}

async function getGenerationJob(request: Request, env: Env, caseId: string, jobId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  const row = await env.DB.prepare("SELECT id, status, stage, progress, error, result_json FROM generation_jobs WHERE id = ? AND case_id = ?")
    .bind(jobId, caseId).first<{ id: string; status: string; stage: string; progress: number; error: string | null; result_json: string }>();
  if (!row) return json({ detail: "生成任务不存在" }, { status: 404 });
  return json({ id: row.id, status: row.status, stage: row.stage, progress: row.progress, error: row.error, result: parseCaseData(row.result_json) });
}

async function getActiveGenerationJob(request: Request, env: Env, caseId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  await failStaleGenerationJobs(env, caseId);
  const row = await env.DB.prepare(
    "SELECT id, status, stage, progress, error FROM generation_jobs WHERE case_id = ? AND status IN ('queued', 'dispatched', 'running', 'finalizing') ORDER BY updated_at DESC LIMIT 1",
  ).bind(caseId).first<{ id: string; status: string; stage: string; progress: number; error: string | null }>();
  return json(row ? { id: row.id, status: row.status, stage: row.stage, progress: row.progress, error: row.error } : null);
}

async function downloadArtifact(request: Request, env: Env, caseId: string, artifactId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  const row = await env.DB.prepare("SELECT filename, object_key FROM artifacts WHERE id = ? AND case_id = ?")
    .bind(artifactId, caseId).first<{ filename: string; object_key: string }>();
  if (!row) return json({ detail: "文件不存在" }, { status: 404 });
  const object = await env.FILES.get(row.object_key);
  if (!object) return json({ detail: "文件不存在" }, { status: 404 });
  const headers = new Headers({ "Content-Type": object.httpMetadata?.contentType || "application/octet-stream", "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(row.filename)}` });
  return new Response(object.body, { headers });
}

async function legalSearch(request: Request, env: Env, caseId: string): Promise<Response> {
  const owned = await ownedCase(request, env, caseId);
  if (owned instanceof Response) return owned;
  let body: { query?: unknown };
  try { body = await request.json() as typeof body; } catch { return json({ detail: "请求体必须是 JSON" }, { status: 400 }); }
  const query = typeof body.query === "string" ? body.query.trim().slice(0, 500) : "";
  if (!query) return json({ detail: "请输入检索词" }, { status: 422 });
  let research: Record<string, unknown>;
  try {
    research = await requestLegalSearch(env, query);
  } catch (error) {
    const detail = error instanceof Error ? error.message : "法律核验服务暂时不可用";
    return json({ detail }, { status: 503 });
  }
  const law = research.law;
  const cases = research.cases;
  if (!law || typeof law !== "object" || Array.isArray(law)) {
    return json({ detail: "法律核验服务未返回法条结果" }, { status: 502 });
  }
  const data = parseCaseData(owned.row.data_json);
  const snapshots = Array.isArray(data.legal_snapshots) ? [...data.legal_snapshots] : [];
  const snapshot = law as Record<string, unknown>;
  snapshots.push(snapshot);
  data.legal_snapshots = snapshots.slice(-20);
  data.legal_research = {
    law: snapshot,
    cases: cases && typeof cases === "object" && !Array.isArray(cases) ? cases : null,
  };
  await env.DB.prepare("UPDATE cases SET data_json = ? WHERE id = ? AND user_id = ?").bind(JSON.stringify(data), caseId, owned.user.id).run();
  return json({ snapshot, research: data.legal_research, readiness: readinessFor(owned.row, data).readiness });
}

function isAuthorized(request: Request, env: Env): boolean {
  if (env.ENVIRONMENT === "local") {
    return true;
  }
  const expected = env.INTERNAL_API_TOKEN?.trim();
  return Boolean(expected && request.headers.get("X-Internal-Token") === expected);
}

async function enqueueGeneration(request: Request, env: Env): Promise<Response> {
  if (!isAuthorized(request, env)) {
    return json({ detail: "未授权" }, { status: 401 });
  }

  let body: { case_id?: string };
  try {
    body = (await request.json()) as { case_id?: string };
  } catch {
    return json({ detail: "请求体必须是 JSON" }, { status: 400 });
  }

  const caseId = body.case_id?.trim();
  if (!caseId) {
    return json({ detail: "缺少 case_id" }, { status: 400 });
  }

  const existingCase = await env.DB.prepare("SELECT c.id, c.generation_count, c.access_status, c.expires_at, u.email FROM cases c JOIN users u ON u.id = c.user_id WHERE c.id = ?")
    .bind(caseId)
    .first<{ id: string; generation_count: number; access_status: string; expires_at: string; email: string }>();
  if (!existingCase) {
    return json({ detail: "案件不存在" }, { status: 404 });
  }
  if (new Date(existingCase.expires_at).getTime() <= Date.now()) {
    return json({ detail: "案件已经到期" }, { status: 410 });
  }
  const unlimited = isTestAdminEmail(env, existingCase.email);
  if (unlimited) {
    existingCase.access_status = "free";
    existingCase.generation_count = 0;
  }
  if (!unlimited && existingCase.access_status === "locked") {
    return json({ detail: "请先使用兑换码解锁该案件" }, { status: 402 });
  }
  if (!unlimited && existingCase.generation_count >= 3) {
    return json({ detail: "本案件最多成功生成3次" }, { status: 429 });
  }
  const runningJob = await env.DB.prepare(
    "SELECT id FROM generation_jobs WHERE case_id = ? AND status IN ('queued', 'running', 'dispatched', 'finalizing') LIMIT 1",
  )
    .bind(caseId)
    .first<{ id: string }>();
  if (runningJob) {
    return json({ detail: "已有生成任务正在处理中" }, { status: 409 });
  }

  const jobId = crypto.randomUUID();
  const now = new Date().toISOString();
  await env.DB.prepare(
    "INSERT INTO generation_jobs (id, case_id, status, stage, progress, created_at, updated_at) VALUES (?, ?, 'queued', 'queued', 5, ?, ?)",
  )
    .bind(jobId, caseId, now, now)
    .run();

  try {
    await env.GENERATION_QUEUE.send({
      type: "generation",
      version: 1,
      job_id: jobId,
      case_id: caseId,
      requested_at: now,
    });
  } catch (error) {
    await markGenerationJobFailed(env, jobId, caseId, `入队失败：${String(error)}`);
    return json({ detail: "生成任务入队失败", job_id: jobId }, { status: 503 });
  }

  return json({ job_id: jobId, status: "queued" }, { status: 202 });
}

async function getGenerationInput(request: Request, env: Env, caseId: string): Promise<Response> {
  if (!isAuthorized(request, env)) {
    return json({ detail: "未授权" }, { status: 401 });
  }

  const row = await env.DB.prepare(
    "SELECT id, title, case_stage, party_side, status, access_status, data_json, generation_count, generation_version, created_at, expires_at FROM cases WHERE id = ?",
  )
    .bind(caseId)
    .first<{
      id: string;
      title: string;
      case_stage: string;
      party_side: string;
      status: string;
      access_status: string;
      data_json: string;
      generation_count: number;
      generation_version: string;
      created_at: string;
      expires_at: string;
    }>();
  if (!row) {
    return json({ detail: "案件不存在" }, { status: 404 });
  }
  if (new Date(row.expires_at).getTime() <= Date.now()) {
    return json({ detail: "案件已经到期" }, { status: 410 });
  }

  const evidence = await env.DB.prepare(
    "SELECT id, original_name, name, purpose, object_key, mime_type, size_bytes, sha256, status, processing_stage, processing_progress, analysis_json, created_at FROM evidence WHERE case_id = ? ORDER BY created_at, id",
  )
    .bind(caseId)
    .all<{
      id: string;
      original_name: string;
      name: string;
      purpose: string;
      object_key: string;
      mime_type: string;
      size_bytes: number;
      sha256: string;
      status: string;
      processing_stage: string;
      processing_progress: number;
      analysis_json: string;
      created_at: string;
    }>();

  let data: Record<string, unknown>;
  try {
    const parsed = JSON.parse(row.data_json || "{}");
    data = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return json({ detail: "案件数据损坏" }, { status: 500 });
  }

  return json(
    {
      version: 1,
      case: {
        id: row.id,
        title: row.title,
        case_stage: row.case_stage,
        party_side: row.party_side,
        status: row.status,
        access_status: row.access_status,
        data,
        generation_count: row.generation_count,
        generation_version: row.generation_version,
        created_at: row.created_at,
        expires_at: row.expires_at,
      },
      evidence: evidence.results.map((item) => ({
        ...item,
        analysis: parseCaseData(item.analysis_json),
      })),
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}

async function updateGenerationProgress(request: Request, env: Env, jobId: string): Promise<Response> {
  if (!isAuthorized(request, env)) return json({ detail: "未授权" }, { status: 401 });
  let body: { stage?: unknown; progress?: unknown };
  try { body = await request.json() as typeof body; } catch { return json({ detail: "请求体必须是 JSON" }, { status: 400 }); }
  const stage = typeof body.stage === "string" ? body.stage.trim().slice(0, 80) : "processing";
  const progress = Math.max(1, Math.min(99, Math.round(Number(body.progress) || 1)));
  await env.DB.prepare("UPDATE generation_jobs SET status = 'running', stage = ?, progress = ?, updated_at = ? WHERE id = ? AND status NOT IN ('completed', 'finalizing', 'failed')")
    .bind(stage, progress, new Date().toISOString(), jobId).run();
  return json({ status: "ok" });
}

function generationDataPatch(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const allowed = new Set([
    "parties",
    "employment_facts",
    "arbitration",
    "court",
    "claims",
    "legal_basis",
    "evidence_gaps",
    "evidence_requirements",
    "jurisdiction",
    "_ai_draft",
  ]);
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>)
      .filter(([key, item]) => allowed.has(key) && item !== null && item !== undefined),
  );
}

async function dispatchEvidenceAnalysis(message: Message<EvidenceAnalysisMessage>, env: Env): Promise<void> {
  const { evidence_id: evidenceId, case_id: caseId } = message.body;
  await env.DB.prepare("UPDATE evidence SET status = 'processing', processing_stage = 'extracting', processing_progress = 25 WHERE id = ? AND case_id = ?")
    .bind(evidenceId, caseId).run();
  const generatorUrl = env.GENERATOR_URL?.trim();
  if (!generatorUrl) {
    await env.DB.prepare("UPDATE evidence SET status = 'failed', processing_stage = 'failed', processing_progress = 100 WHERE id = ?")
      .bind(evidenceId).run();
    message.ack();
    return;
  }
  const headers = new Headers({ "Content-Type": "application/json" });
  if (env.GENERATOR_AUTH_TOKEN) headers.set("Authorization", `Bearer ${env.GENERATOR_AUTH_TOKEN}`);

  const failEvidence = async (detail: string): Promise<void> => {
    const error = detail.slice(0, 1000);
    await env.DB.batch([
      env.DB.prepare("UPDATE evidence SET status = 'failed', processing_stage = 'failed', processing_progress = 100, analysis_json = ? WHERE id = ? AND case_id = ?")
        .bind(JSON.stringify({ error }), evidenceId, caseId),
      env.DB.prepare("UPDATE cases SET status = CASE WHEN NOT EXISTS (SELECT 1 FROM evidence WHERE case_id = ? AND status IN ('queued', 'processing')) THEN 'ready_to_generate' ELSE status END WHERE id = ?")
        .bind(caseId, caseId),
    ]);
  };

  const retryOrFail = async (detail: string): Promise<void> => {
    if (message.attempts >= EVIDENCE_ANALYSIS_MAX_ATTEMPTS) {
      await failEvidence(`材料识别失败：${detail}。请删除该材料后重新上传。`);
      message.ack();
      return;
    }
    await env.DB.prepare("UPDATE evidence SET status = 'processing', processing_stage = 'retrying', processing_progress = 55 WHERE id = ? AND case_id = ?")
      .bind(evidenceId, caseId).run();
    message.retry({ delaySeconds: Math.min(60, Math.max(5, message.attempts * 10)) });
  };

  let response: Response;
  try {
    await env.DB.prepare("UPDATE evidence SET processing_stage = 'analyzing', processing_progress = 55 WHERE id = ?").bind(evidenceId).run();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), EVIDENCE_ANALYSIS_TIMEOUT_MS);
    try {
      response = await fetch(`${generatorUrl.replace(/\/$/, "")}/internal/evidence-analysis`, {
      method: "POST", headers, body: JSON.stringify({ version: 1, evidence_id: evidenceId, case_id: caseId }),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }
  } catch (error) {
    const detail = error instanceof DOMException && error.name === "AbortError"
      ? `分析服务超过 ${Math.round(EVIDENCE_ANALYSIS_TIMEOUT_MS / 1000)} 秒未响应`
      : `分析服务请求失败：${String(error)}`;
    await retryOrFail(detail);
    return;
  }
  if (!response.ok) {
    const detail = (await response.text()).slice(0, 1000);
    if (response.status >= 400 && response.status < 500) {
      await failEvidence(`分析服务返回 ${response.status}：${detail}`);
      message.ack();
      return;
    }
    await retryOrFail(`分析服务返回 ${response.status}`);
    return;
  }
  let result: { name?: unknown; purpose?: unknown; analysis?: unknown };
  try {
    result = await response.json() as typeof result;
  } catch (error) {
    await retryOrFail(`分析服务返回格式无效：${String(error)}`);
    return;
  }
  const name = typeof result.name === "string" ? result.name.trim().slice(0, 255) : "材料";
  const purpose = typeof result.purpose === "string" ? result.purpose.trim().slice(0, 2000) : "[待核实证明目的]";
  await env.DB.batch([
    env.DB.prepare("UPDATE evidence SET name = ?, purpose = ?, status = 'ready', processing_stage = 'complete', processing_progress = 100, analysis_json = ? WHERE id = ? AND case_id = ?")
      .bind(name || "材料", purpose, JSON.stringify(result.analysis || {}), evidenceId, caseId),
    env.DB.prepare("UPDATE cases SET status = CASE WHEN NOT EXISTS (SELECT 1 FROM evidence WHERE case_id = ? AND status IN ('queued', 'processing') AND id <> ?) THEN 'ready_to_generate' ELSE status END WHERE id = ?")
      .bind(caseId, evidenceId, caseId),
  ]);
  message.ack();
}

async function finalizeGenerationResult(
  env: Env,
  jobId: string,
  caseId: string,
  result: unknown,
): Promise<void> {
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    throw new Error("生成服务缺少 result");
  }
  const rawArtifacts = (result as { artifacts?: unknown }).artifacts;
  if (!Array.isArray(rawArtifacts)) {
    throw new Error("生成服务返回的文书列表无效");
  }
  const evidenceUpdates = Array.isArray((result as { evidence_updates?: unknown }).evidence_updates)
    ? (result as { evidence_updates: unknown[] }).evidence_updates.flatMap((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return [];
       const candidate = item as {
         id?: unknown;
         name?: unknown;
         purpose?: unknown;
         included?: unknown;
         order?: unknown;
         analysis?: unknown;
       };
      const id = typeof candidate.id === "string" ? candidate.id : "";
      const name = typeof candidate.name === "string" ? candidate.name.trim().slice(0, 255) : "";
      const purpose = typeof candidate.purpose === "string" ? candidate.purpose.trim().slice(0, 2000) : "";
       const analysis = candidate.analysis && typeof candidate.analysis === "object" && !Array.isArray(candidate.analysis)
         ? candidate.analysis as Record<string, unknown>
         : null;
       return id && name && purpose
         ? [{ id, name, purpose, included: candidate.included !== false, order: Number.isFinite(Number(candidate.order)) ? Number(candidate.order) : null, analysis }]
         : [];
    })
    : [];
  const artifacts = rawArtifacts.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const candidate = item as { kind?: unknown; filename?: unknown; object_key?: unknown };
    const kind = typeof candidate.kind === "string" ? candidate.kind.trim().slice(0, 50) : "";
    const filename = typeof candidate.filename === "string" ? candidate.filename.trim().slice(0, 255) : "";
    const objectKey = typeof candidate.object_key === "string" ? candidate.object_key.trim().slice(0, 1000) : "";
    return kind && filename && objectKey ? [{ kind, filename, object_key: objectKey }] : [];
  });
  if (artifacts.length !== rawArtifacts.length) {
    throw new Error("生成服务返回的文书元数据无效");
  }

  const current = await env.DB.prepare("SELECT case_id, status FROM generation_jobs WHERE id = ?")
    .bind(jobId)
    .first<{ case_id: string; status: string }>();
  if (!current || current.case_id !== caseId) {
    throw new Error("生成任务不存在或案件编号不一致");
  }
  if (current.status === "completed") return;

  const caseRow = await env.DB.prepare("SELECT data_json FROM cases WHERE id = ?")
    .bind(caseId)
    .first<{ data_json: string }>();
  const patch = generationDataPatch((result as { data_patch?: unknown }).data_patch);
  const mergedCaseData = caseRow && Object.keys(patch).length
    ? mergeData(parseCaseData(caseRow.data_json), patch)
    : null;

  const claimed = await env.DB.prepare(
    "UPDATE generation_jobs SET status = 'finalizing', stage = 'finalizing', progress = 99, updated_at = ? WHERE id = ? AND case_id = ? AND status NOT IN ('completed', 'finalizing')",
  ).bind(new Date().toISOString(), jobId, caseId).run();
  if (!claimed.meta.changes) return;

  const completedAt = new Date().toISOString();
  const caseUpdate = mergedCaseData
    ? env.DB.prepare("UPDATE cases SET data_json = ?, status = 'generated', generation_count = generation_count + 1 WHERE id = ?")
      .bind(JSON.stringify(mergedCaseData), caseId)
    : env.DB.prepare("UPDATE cases SET status = 'generated', generation_count = generation_count + 1 WHERE id = ?")
      .bind(caseId);
  await env.DB.batch([
    env.DB.prepare(
      "UPDATE generation_jobs SET status = 'completed', stage = 'complete', progress = 100, error = NULL, result_json = ?, updated_at = ? WHERE id = ?",
    ).bind(JSON.stringify(result), completedAt, jobId),
    env.DB.prepare("DELETE FROM artifacts WHERE case_id = ?").bind(caseId),
    caseUpdate,
    ...evidenceUpdates.map((item) =>
      env.DB.prepare("UPDATE evidence SET name = ?, purpose = ?, analysis_json = CASE WHEN ? IS NULL THEN analysis_json ELSE ? END WHERE id = ? AND case_id = ?")
        .bind(
          item.name,
          item.purpose,
          item.analysis ? JSON.stringify(item.analysis) : null,
          item.analysis ? JSON.stringify(item.analysis) : null,
          item.id,
          caseId,
        ),
    ),
    ...artifacts.map((artifact) =>
      env.DB.prepare(
        "INSERT INTO artifacts (id, case_id, filename, kind, object_key, created_at) VALUES (?, ?, ?, ?, ?, ?)",
      ).bind(crypto.randomUUID(), caseId, artifact.filename, artifact.kind, artifact.object_key, completedAt),
    ),
  ]);
}

async function receiveGenerationResult(request: Request, env: Env, jobId: string): Promise<Response> {
  if (!isAuthorized(request, env)) return json({ detail: "未授权" }, { status: 401 });
  let body: { case_id?: unknown; status?: unknown; result?: unknown; error?: unknown };
  try {
    body = await request.json() as typeof body;
  } catch {
    return json({ detail: "请求体必须是 JSON" }, { status: 400 });
  }
  const caseId = typeof body.case_id === "string" ? body.case_id.trim() : "";
  if (!caseId) return json({ detail: "缺少 case_id" }, { status: 400 });

  if (body.status === "failed") {
    const error = typeof body.error === "string" ? body.error.slice(0, 1000) : "文书生成失败";
    await markGenerationJobFailed(env, jobId, caseId, error);
    return json({ status: "ok" });
  }
  if (body.status !== "completed") return json({ detail: "无效的生成状态" }, { status: 400 });

  try {
    await finalizeGenerationResult(env, jobId, caseId, body.result);
  } catch (error) {
    const detail = String(error).slice(0, 1000);
    await markGenerationJobFailed(env, jobId, caseId, detail);
    return json({ detail }, { status: 400 });
  }
  return json({ status: "ok" });
}

async function dispatchGeneration(message: Message<GenerationMessage>, env: Env): Promise<void> {
  const { job_id: jobId, case_id: caseId } = message.body;
  const now = new Date().toISOString();
  await env.DB.prepare("UPDATE generation_jobs SET status = 'running', stage = 'preparing', progress = 10, updated_at = ? WHERE id = ?")
    .bind(now, jobId)
    .run();

  const generatorUrl = env.GENERATOR_URL?.trim();
  if (!generatorUrl) {
    await markGenerationJobFailed(env, jobId, caseId, "生成服务地址未配置");
    message.ack();
    return;
  }

  const headers = new Headers({ "Content-Type": "application/json" });
  if (env.GENERATOR_AUTH_TOKEN) {
    headers.set("Authorization", `Bearer ${env.GENERATOR_AUTH_TOKEN}`);
  }

  let response: Response;
  try {
    response = await fetch(`${generatorUrl.replace(/\/$/, "")}/internal/generation-jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify({ version: 1, job_id: jobId, case_id: caseId }),
    });
  } catch (error) {
    await markGenerationJobFailed(env, jobId, caseId, `生成服务不可达：${String(error)}`);
    throw error;
  }

  if (!response.ok) {
    const body = (await response.text()).slice(0, 1500);
    let detail = body;
    try {
      const parsed = JSON.parse(body) as { detail?: unknown };
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch { /* keep the safe response body */ }
    await markGenerationJobFailed(env, jobId, caseId, `生成服务返回 ${response.status}：${detail}`);
    if (response.status >= 400 && response.status < 500) {
      message.ack();
      return;
    }
    throw new Error(`generator returned ${response.status}`);
  }

  let responseBody: { status?: unknown; result?: unknown };
  try {
    responseBody = (await response.json()) as { status?: unknown; result?: unknown };
  } catch {
    await markGenerationJobFailed(env, jobId, caseId, "生成服务返回格式错误");
    message.ack();
    return;
  }

  if (responseBody.status === "completed") {
    try {
      await finalizeGenerationResult(env, jobId, caseId, responseBody.result);
    } catch (error) {
      await markGenerationJobFailed(env, jobId, caseId, String(error));
    }
  } else {
    await env.DB.prepare(
      "UPDATE generation_jobs SET status = 'dispatched', result_json = ?, updated_at = ? WHERE id = ? AND status NOT IN ('completed', 'failed', 'finalizing')",
    )
      .bind(JSON.stringify(responseBody), new Date().toISOString(), jobId)
      .run();
  }
  message.ack();
}

async function purgeExpiredCases(env: Env): Promise<void> {
  const now = new Date().toISOString();
  const expired = await env.DB.prepare("SELECT id FROM cases WHERE expires_at <= ? LIMIT 50")
    .bind(now)
    .all<{ id: string }>();

  for (const row of expired.results) {
    const objects = await env.DB.prepare(
      "SELECT object_key FROM evidence WHERE case_id = ? UNION ALL SELECT object_key FROM artifacts WHERE case_id = ?",
    )
      .bind(row.id, row.id)
      .all<{ object_key: string }>();
    try {
      await Promise.all([...new Set(objects.results.map((item) => item.object_key).filter(Boolean))].map((key) => env.FILES.delete(key)));
    } catch {
      continue;
    }

    await env.DB.batch([
      env.DB.prepare("UPDATE redemption_codes SET redeemed_case_id = NULL WHERE redeemed_case_id = ?").bind(row.id),
      env.DB.prepare("DELETE FROM generation_jobs WHERE case_id = ?").bind(row.id),
      env.DB.prepare("DELETE FROM notifications WHERE case_id = ?").bind(row.id),
      env.DB.prepare("DELETE FROM evidence WHERE case_id = ?").bind(row.id),
      env.DB.prepare("DELETE FROM artifacts WHERE case_id = ?").bind(row.id),
      env.DB.prepare("DELETE FROM cases WHERE id = ?").bind(row.id),
    ]);
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/api/health") {
      try {
        const row = await env.DB.prepare("SELECT 1 AS ok").first<{ ok: number }>();
        return json({
          status: "ok",
          database: row?.ok === 1 ? "ok" : "unavailable",
          storage: env.FILES ? "configured" : "unavailable",
          queue: env.GENERATION_QUEUE ? "configured" : "unavailable",
          environment: env.ENVIRONMENT,
        });
      } catch {
        return json({ status: "degraded", database: "unavailable", environment: env.ENVIRONMENT }, { status: 503 });
      }
    }

    if (request.method === "POST" && url.pathname === "/api/auth/request-code") {
      return requestAuthCode(request, env);
    }

    if (request.method === "POST" && url.pathname === "/api/auth/register-code") {
      return requestRegistrationCode(request, env);
    }

    if (request.method === "POST" && url.pathname === "/api/auth/register") {
      return registerPassword(request, env);
    }

    if (request.method === "POST" && url.pathname === "/api/auth/login") {
      return loginPassword(request, env);
    }

    if (request.method === "POST" && url.pathname === "/api/auth/verify") {
      return verifyAuthCode(request, env);
    }

    if (request.method === "POST" && url.pathname === "/api/auth/logout") {
      return new Response(null, {
        status: 204,
        headers: { "Set-Cookie": sessionCookie("", 0) },
      });
    }

    if (request.method === "GET" && url.pathname === "/api/auth/me") {
      const user = await currentUser(request, env);
      return user ? json(user) : json({ detail: "请先登录" }, { status: 401 });
    }

    if (request.method === "GET" && url.pathname === "/api/cases") {
      return listCases(request, env);
    }

    if (request.method === "POST" && url.pathname === "/api/cases") {
      return createCase(request, env);
    }

    const caseMatch = url.pathname.match(/^\/api\/cases\/([^/]+)(?:\/(.*))?$/);
    if (caseMatch) {
      const caseId = decodeURIComponent(caseMatch[1]);
      const subpath = caseMatch[2] ?? "";
      if (request.method === "GET" && !subpath) {
        const owned = await ownedCase(request, env, caseId);
        return owned instanceof Response ? owned : json(await casePayload(env, owned.row, owned.user));
      }
      if (request.method === "DELETE" && !subpath) return deleteCase(request, env, caseId);
      if (request.method === "PATCH" && !subpath) return updateCase(request, env, caseId);
      if (request.method === "POST" && subpath === "chat") return chatCase(request, env, caseId);
      if (request.method === "POST" && subpath === "evidence") return uploadEvidence(request, env, caseId);
      if (request.method === "POST" && subpath === "generate") return generateCase(request, env, caseId);
      if (request.method === "POST" && subpath === "legal-search") return legalSearch(request, env, caseId);
      if (request.method === "GET" && subpath === "generation-jobs/active") {
        return getActiveGenerationJob(request, env, caseId);
      }
      if (request.method === "GET" && subpath.startsWith("generation-jobs/")) {
        return getGenerationJob(request, env, caseId, decodeURIComponent(subpath.slice("generation-jobs/".length)));
      }
      if (request.method === "GET" && subpath.startsWith("artifacts/")) {
        return downloadArtifact(request, env, caseId, decodeURIComponent(subpath.slice("artifacts/".length)));
      }
      const evidenceMatch = subpath.match(/^evidence\/([^/]+)$/);
      if (evidenceMatch && request.method === "PATCH") return updateEvidence(request, env, caseId, decodeURIComponent(evidenceMatch[1]));
      if (evidenceMatch && request.method === "DELETE") return deleteEvidence(request, env, caseId, decodeURIComponent(evidenceMatch[1]));
    }

    if (request.method === "POST" && url.pathname === "/api/internal/generation-jobs") {
      return enqueueGeneration(request, env);
    }

    const generationProgressMatch = url.pathname.match(/^\/api\/internal\/generation-jobs\/([^/]+)\/progress$/);
    if (request.method === "POST" && generationProgressMatch) {
      return updateGenerationProgress(request, env, decodeURIComponent(generationProgressMatch[1]));
    }

    const generationResultMatch = url.pathname.match(/^\/api\/internal\/generation-jobs\/([^/]+)\/result$/);
    if (request.method === "POST" && generationResultMatch) {
      return receiveGenerationResult(request, env, decodeURIComponent(generationResultMatch[1]));
    }

    const generationInputMatch = url.pathname.match(/^\/api\/internal\/cases\/([^/]+)\/generation-input$/);
    if (request.method === "GET" && generationInputMatch) {
      return getGenerationInput(request, env, decodeURIComponent(generationInputMatch[1]));
    }

    const evidenceAnalysisMatch = url.pathname.match(/^\/api\/internal\/cases\/([^/]+)\/evidence\/([^/]+)\/analysis$/);
    if (request.method === "POST" && evidenceAnalysisMatch) {
      return cacheEvidenceAnalysis(
        request,
        env,
        decodeURIComponent(evidenceAnalysisMatch[1]),
        decodeURIComponent(evidenceAnalysisMatch[2]),
      );
    }

    return json({ detail: "Cloudflare Worker API迁移中" }, { status: 501 });
  },

  async queue(batch: MessageBatch<QueueMessage>, env: Env): Promise<void> {
    for (const message of batch.messages) {
      if (message.body.type === "evidence_analysis") {
        await dispatchEvidenceAnalysis(message as Message<EvidenceAnalysisMessage>, env);
      } else {
        await dispatchGeneration(message as Message<GenerationMessage>, env);
      }
    }
  },

  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    await purgeExpiredCases(env);
  },
} satisfies ExportedHandler<Env, QueueMessage>;
