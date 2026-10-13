import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { after, before, test } from "node:test";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";
import ts from "typescript";

const secret = "admin-test-session-secret";
const source = readFileSync(new URL("../src/index.ts", import.meta.url), "utf8");
const mf = new Miniflare(convertV4MiniflareOptions({
  modules: true,
  script: ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText,
  compatibilityDate: "2026-08-11",
  d1Databases: ["DB"],
  bindings: { SESSION_SECRET: secret, TEST_ADMIN_EMAIL: "admin@example.com", TEST_ADMIN_ENABLED: "true", TEST_ADMIN_PASSWORD: "test-password-123" },
}));

function cookie(id, email, expired = false) {
  const payload = Buffer.from(JSON.stringify({ uid: id, email, exp: Math.floor(Date.now() / 1000) + (expired ? -60 : 3600) })).toString("base64url");
  return `labor_session=${payload}.${createHmac("sha256", secret).update(payload).digest("base64url")}`;
}

const adminCookie = cookie("admin", "admin@example.com");
function overview(query = "", session = adminCookie) {
  return mf.dispatchFetch(`https://test.example/api/admin/overview${query}`, { headers: session ? { Cookie: session } : {} });
}

before(async () => {
  const db = await mf.getD1Database("DB");
  const migrations = new URL("../migrations/", import.meta.url);
  for (const file of readdirSync(migrations).filter((name) => name.endsWith(".sql")).sort()) {
    const sql = readFileSync(new URL(file, migrations), "utf8").replace(/--[^\n]*/g, "");
    for (const statement of sql.split(";").filter((part) => part.trim())) await db.prepare(statement).run();
  }
  const now = new Date().toISOString();
  const old = new Date(Date.now() - 40 * 86400000).toISOString();
  await db.prepare("INSERT INTO users (id,email,created_at) VALUES ('admin','admin@example.com',?)").bind(now).run();
  for (let index = 1; index <= 23; index++) {
    const id = `user${String(index).padStart(2, "0")}`;
    await db.prepare("INSERT INTO users (id,email,created_at,email_verified,password_hash) VALUES (?,?,?,1,'private-password-hash')")
      .bind(id, index === 23 ? "literal%name@example.com" : `${id}@example.com`, index === 1 ? now : old).run();
  }
  for (const [id, user, count] of [["case1", "user01", 3], ["case2", "admin", 1]]) {
    await db.prepare("INSERT INTO cases (id,user_id,title,case_stage,party_side,generation_count,created_at,expires_at,data_json) VALUES (?,?,'private-case-title','arbitration','worker',?,?,?,'{\"facts\":\"private-case-facts\"}')")
      .bind(id, user, count, now, new Date(Date.now() + 86400000).toISOString()).run();
  }
  for (const [id, status] of [["job1", "completed"], ["job2", "failed"], ["job3", "queued"]]) {
    await db.prepare("INSERT INTO generation_jobs (id,case_id,status,error,created_at,updated_at) VALUES (?,'case1',?,'private-provider-error',?,?)").bind(id, status, now, now).run();
  }
});

after(async () => { await mf.dispose(); });

test("anonymous, expired, forged, deleted-user and ordinary sessions cannot read admin data", async () => {
  for (const session of [null, cookie("admin", "admin@example.com", true), `${adminCookie}tampered`, cookie("missing", "admin@example.com")]) {
    const response = await overview("", session);
    assert.equal(response.status, 401);
    assert.match(response.headers.get("Cache-Control"), /no-store/);
    assert.equal((await response.json()).stats, undefined);
  }
  // Permission follows the database email, not an email claimed in the cookie.
  const response = await overview("", cookie("user01", "admin@example.com"));
  assert.equal(response.status, 403);
  assert.equal((await response.json()).users, undefined);
});

test("existing admin login grants access to accurate overview without private case data", async () => {
  const login = await mf.dispatchFetch("https://test.example/api/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: "admin@example.com", password: "test-password-123" }),
  });
  assert.equal(login.status, 200);
  const response = await overview("", login.headers.get("Set-Cookie").split(";")[0]);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Vary"), "Cookie");
  const text = await response.text();
  const data = JSON.parse(text);
  assert.deepEqual(data.stats, { total_accounts: 24, registered_users: 23, new_users_7d: 1, retained_cases: 2, accounts_with_cases: 2, successful_generations: 4, pending_jobs: 1, failed_jobs: 1 });
  assert.equal(data.users.length, 20);
  assert.equal(data.pagination.total, 24);
  assert.equal(data.recent_jobs.length, 3);
  assert.doesNotMatch(text, /private-password-hash|private-case-title|private-case-facts|private-provider-error/);
});

test("email search and pagination return bounded results and treat SQL and wildcard characters literally", async () => {
  const secondPage = await (await overview("?page=2")).json();
  assert.equal(secondPage.users.length, 4);
  const firstPage = await (await overview()).json();
  assert.equal(new Set([...firstPage.users, ...secondPage.users].map((user) => user.id)).size, 24);
  const found = await (await overview("?search=user01")).json();
  assert.equal(found.pagination.total, 1);
  assert.equal(found.users[0].email, "user01@example.com");
  const literal = await (await overview("?search=%25")).json();
  assert.equal(literal.pagination.total, 1);
  const injection = await (await overview(`?search=${encodeURIComponent("' OR 1=1 --")}`)).json();
  assert.equal(injection.pagination.total, 0);
  for (const query of ["?page=0", "?page=1.5", "?page=abc", `?search=${"x".repeat(255)}`]) assert.equal((await overview(query)).status, 400);
});

test("auth/me exposes admin navigation only for the configured administrator", async () => {
  const admin = await mf.dispatchFetch("https://test.example/api/auth/me", { headers: { Cookie: adminCookie } });
  assert.equal((await admin.json()).is_admin, true);
  const ordinary = await mf.dispatchFetch("https://test.example/api/auth/me", { headers: { Cookie: cookie("user01", "user01@example.com") } });
  assert.equal((await ordinary.json()).is_admin, false);
});
