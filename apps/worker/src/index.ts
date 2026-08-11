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
