# Cloudflare 预发布部署手册

以下命令在 `apps/worker` 目录执行。首次执行前先登录目标 Cloudflare 账号，并确认账号、R2 和 D1 的数据处理区域符合业务隐私要求。

## 1. 创建资源

```powershell
npm install
npx wrangler d1 create labor-docs
npx wrangler r2 bucket create labor-docs-files
npx wrangler queues create labor-docs-generation
```

把 `d1 create` 返回的真实 UUID 写入 `wrangler.jsonc` 的生产 `database_id`。桶名和队列名必须与配置一致；预发布环境建议使用独立名称。

## 2. 应用数据库迁移

```powershell
$env:CI='1'
npx wrangler d1 migrations apply labor-docs --remote
```

迁移完成后抽查 `users.free_case_used`、`otp_codes.attempts` 和案件/证据外键。

## 3. 配置 Worker Secret 和变量

```powershell
npx wrangler secret put INTERNAL_API_TOKEN
npx wrangler secret put GENERATOR_AUTH_TOKEN
npx wrangler deploy --env="" --var GENERATOR_URL:https://<generator-host>
```

两个 Secret 不要写进 `wrangler.jsonc`、前端代码、日志或工单。`GENERATOR_URL` 必须指向只允许 Worker 访问的生成服务入口。

## 4. 配置 FastAPI 生成服务

生产环境至少设置：

```text
APP_ENV=production
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_BUCKET=labor-docs-files
S3_ACCESS_KEY=<R2 access key>
S3_SECRET_KEY=<R2 secret>
S3_REGION=auto
GENERATOR_INTERNAL_TOKEN=<与 GENERATOR_AUTH_TOKEN 相同>
WORKER_INTERNAL_URL=https://<worker-host>
WORKER_INTERNAL_TOKEN=<与 INTERNAL_API_TOKEN 相同>
```

先用预发布 Worker 完成“上传实际材料 → Queue → FastAPI 生成 → R2 文书 → D1 元数据 → 下载”闭环，再切换前端 API 地址。

## 5. 回滚与清理

- Worker 发布前保留上一个版本；发现生成失败时先暂停前端生成入口，不删除 D1/R2 数据。
- 迁移失败时停止发布，记录失败迁移名称；不要手工删除 D1 表。
- 预发布测试数据使用单独账户和桶，测试完成后通过 Worker Cron 或受控清理脚本删除。
