interface GenerationMessage {
  version: 1;
  job_id: string;
  case_id: string;
  requested_at: string;
}

interface Env {
  DB: D1Database;
  FILES: R2Bucket;
  GENERATION_QUEUE: Queue<GenerationMessage>;
  ENVIRONMENT: string;
  GENERATOR_URL?: string;
  GENERATOR_AUTH_TOKEN?: string;
  INTERNAL_API_TOKEN?: string;
  SESSION_SECRET?: string;
}

const SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;

type PublicUser = { id: string; email: string };

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
  return user ?? null;
}

async function sendOtpEmail(env: Env, email: string, code: string): Promise<boolean> {
  const generatorUrl = env.GENERATOR_URL?.trim();
  const generatorToken = env.GENERATOR_AUTH_TOKEN?.trim();
  if (!generatorUrl || !generatorToken) return false;
  try {
    const response = await fetch(`${generatorUrl.replace(/\/$/, "")}/internal/auth/send-otp`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${generatorToken}`,
      },
      body: JSON.stringify({ email, code }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function requestAuthCode(request: Request, env: Env): Promise<Response> {
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
  if (!(await sendOtpEmail(env, email, code))) {
    await env.DB.prepare("DELETE FROM otp_codes WHERE id = ?").bind(id).run();
    return json({ detail: "验证码邮件服务暂未配置或发送失败" }, { status: 503 });
  }
  return json({ message: "验证码已发送，有效期 10 分钟" });
}

async function verifyAuthCode(request: Request, env: Env): Promise<Response> {
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
    "SELECT id, email, code_hash, expires_at, attempts FROM otp_codes WHERE email = ? AND consumed_at IS NULL ORDER BY expires_at DESC LIMIT 1",
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

function json(data: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...init.headers,
    },
  });
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

  const existingCase = await env.DB.prepare("SELECT id, generation_count, access_status, expires_at FROM cases WHERE id = ?")
    .bind(caseId)
    .first<{ id: string; generation_count: number; access_status: string; expires_at: string }>();
  if (!existingCase) {
    return json({ detail: "案件不存在" }, { status: 404 });
  }
  if (new Date(existingCase.expires_at).getTime() <= Date.now()) {
    return json({ detail: "案件已经到期" }, { status: 410 });
  }
  if (existingCase.access_status === "locked") {
    return json({ detail: "请先使用兑换码解锁该案件" }, { status: 402 });
  }
  if (existingCase.generation_count >= 3) {
    return json({ detail: "本案件最多成功生成3次" }, { status: 429 });
  }
  const runningJob = await env.DB.prepare(
    "SELECT id FROM generation_jobs WHERE case_id = ? AND status IN ('queued', 'running', 'dispatched') LIMIT 1",
  )
    .bind(caseId)
    .first<{ id: string }>();
  if (runningJob) {
    return json({ detail: "已有生成任务正在处理中" }, { status: 409 });
  }

  const jobId = crypto.randomUUID();
  const now = new Date().toISOString();
  await env.DB.prepare(
    "INSERT INTO generation_jobs (id, case_id, status, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?)",
  )
    .bind(jobId, caseId, now, now)
    .run();

  try {
    await env.GENERATION_QUEUE.send({
      version: 1,
      job_id: jobId,
      case_id: caseId,
      requested_at: now,
    });
  } catch (error) {
    await env.DB.prepare(
      "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
    )
      .bind(`入队失败：${String(error)}`.slice(0, 2000), new Date().toISOString(), jobId)
      .run();
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
    "SELECT id, original_name, name, source, purpose, object_key, mime_type, size_bytes, sha256, status, created_at FROM evidence WHERE case_id = ? ORDER BY created_at, id",
  )
    .bind(caseId)
    .all<{
      id: string;
      original_name: string;
      name: string;
      source: string;
      purpose: string;
      object_key: string;
      mime_type: string;
      size_bytes: number;
      sha256: string;
      status: string;
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
      evidence: evidence.results,
    },
    { headers: { "Cache-Control": "no-store" } },
  );
}

async function dispatchGeneration(message: Message<GenerationMessage>, env: Env): Promise<void> {
  const { job_id: jobId, case_id: caseId } = message.body;
  const now = new Date().toISOString();
  await env.DB.prepare("UPDATE generation_jobs SET status = 'running', updated_at = ? WHERE id = ?")
    .bind(now, jobId)
    .run();

  const generatorUrl = env.GENERATOR_URL?.trim();
  if (!generatorUrl) {
    await env.DB.prepare(
      "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
    )
      .bind("生成服务地址未配置", new Date().toISOString(), jobId)
      .run();
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
    await env.DB.prepare(
      "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
    )
      .bind(`生成服务不可达：${String(error)}`.slice(0, 2000), new Date().toISOString(), jobId)
      .run();
    throw error;
  }

  if (!response.ok) {
    const detail = (await response.text()).slice(0, 1500);
    await env.DB.prepare(
      "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
    )
      .bind(`生成服务返回 ${response.status}：${detail}`, new Date().toISOString(), jobId)
      .run();
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
    await env.DB.prepare(
      "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
    )
      .bind("生成服务返回格式错误", new Date().toISOString(), jobId)
      .run();
    message.ack();
    return;
  }

  if (responseBody.status === "completed") {
    const result = responseBody.result;
    if (!result || typeof result !== "object" || Array.isArray(result)) {
      await env.DB.prepare(
        "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
      )
        .bind("生成服务缺少 result", new Date().toISOString(), jobId)
        .run();
      message.ack();
      return;
    }
    const rawArtifacts = (result as { artifacts?: unknown }).artifacts;
    if (!Array.isArray(rawArtifacts)) {
      await env.DB.prepare(
        "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
      )
        .bind("生成服务返回的文书列表无效", new Date().toISOString(), jobId)
        .run();
      message.ack();
      return;
    }
    const artifacts = rawArtifacts.flatMap((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return [];
      const candidate = item as { kind?: unknown; filename?: unknown; object_key?: unknown };
      const kind = typeof candidate.kind === "string" ? candidate.kind.trim().slice(0, 50) : "";
      const filename = typeof candidate.filename === "string" ? candidate.filename.trim().slice(0, 255) : "";
      const objectKey = typeof candidate.object_key === "string" ? candidate.object_key.trim().slice(0, 1000) : "";
      return kind && filename && objectKey ? [{ kind, filename, object_key: objectKey }] : [];
    });
    if (artifacts.length !== rawArtifacts.length) {
      await env.DB.prepare(
        "UPDATE generation_jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
      )
        .bind("生成服务返回的文书元数据无效", new Date().toISOString(), jobId)
        .run();
      message.ack();
      return;
    }
    const completedAt = new Date().toISOString();
    const statements = [
      env.DB.prepare(
        "UPDATE generation_jobs SET status = 'completed', result_json = ?, updated_at = ? WHERE id = ?",
      ).bind(JSON.stringify(result), completedAt, jobId),
      env.DB.prepare("DELETE FROM artifacts WHERE case_id = ?").bind(caseId),
      env.DB.prepare(
        "UPDATE cases SET status = 'generated', generation_count = generation_count + 1 WHERE id = ?",
      ).bind(caseId),
      ...artifacts.map((artifact) =>
        env.DB.prepare(
          "INSERT INTO artifacts (id, case_id, filename, kind, object_key, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ).bind(crypto.randomUUID(), caseId, artifact.filename, artifact.kind, artifact.object_key, completedAt),
      ),
    ];
    await env.DB.batch(statements);
  } else {
    await env.DB.prepare(
      "UPDATE generation_jobs SET status = 'dispatched', result_json = ?, updated_at = ? WHERE id = ?",
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

    if (request.method === "POST" && url.pathname === "/api/internal/generation-jobs") {
      return enqueueGeneration(request, env);
    }

    const generationInputMatch = url.pathname.match(/^\/api\/internal\/cases\/([^/]+)\/generation-input$/);
    if (request.method === "GET" && generationInputMatch) {
      return getGenerationInput(request, env, decodeURIComponent(generationInputMatch[1]));
    }

    return json({ detail: "Cloudflare Worker API迁移中" }, { status: 501 });
  },

  async queue(batch: MessageBatch<GenerationMessage>, env: Env): Promise<void> {
    for (const message of batch.messages) {
      await dispatchGeneration(message, env);
    }
  },

  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    await purgeExpiredCases(env);
  },
} satisfies ExportedHandler<Env, GenerationMessage>;
