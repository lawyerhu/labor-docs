# Cloudflare 数据层迁移

## 当前决定

- D1保存账户（含首案免费额度状态）、案件字段、证据元数据、生成任务、文书元数据、兑换码、通知和审计记录。
- R2保存证据原件、处理副本和生成的DOCX/PDF。
- Queue消息只传`case_id`和`job_id`，不传身份证号码、案情正文或文件内容。
- 现有FastAPI暂时保留为文书生成服务，Worker完成业务API迁移后再决定是否迁移生成服务。

生成任务已经固定为“只传 ID”的队列契约：Worker 将 `job_id` 和 `case_id` 写入 D1 后发送到 `labor-docs-generation`，队列消费者再向 `GENERATOR_URL/internal/generation-jobs` 分发同一组 ID。案件正文和文件不放进队列消息，FastAPI 生成服务通过受保护的 `/api/internal/cases/{case_id}/generation-input` 读取结构化案件和 R2 对象键，生成后把结果返回给 Worker，由 Worker 更新 D1。桥接代码已完成，但必须配置 R2、内部地址和令牌后才能切换前端生成入口；生成服务未配置或返回错误时，任务会明确标记失败，不会伪造生成结果。

R2 绑定名为 `FILES`，对象键由案件、材料或文书服务生成；Worker 配置中的桶名仅是部署占位符。生产环境必须创建实际桶、配置短期授权和内部令牌后再启用公网流量。

FastAPI 生成服务的桥接配置要求：`STORAGE_BACKEND=s3`，S3 参数指向 R2；`WORKER_INTERNAL_URL` 指向 Worker 的内部访问地址，`WORKER_INTERNAL_TOKEN` 与 Worker 的 `INTERNAL_API_TOKEN` 一致；`GENERATOR_INTERNAL_TOKEN` 与 Worker 的 `GENERATOR_AUTH_TOKEN` 一致。生成服务地址必须只能由 Worker 访问，并通过网络策略或专用入口限制来源。

## 本地工程

`apps/worker`提供Worker入口、D1绑定配置和初始迁移。

```powershell
cd apps/worker
npm install
npm run typecheck
npm run db:migrate:local
npm run dev
```

本地迁移命令需要非交互环境变量，避免 Wrangler 等待确认：

```powershell
$env:CI='1'
npm run db:migrate:local
```

`npm run dev` 使用 `local` 环境；生产部署必须显式指定目标环境，不会沿用本地生成地址。生产内部令牌通过 `wrangler secret put INTERNAL_API_TOKEN` 配置，生成服务地址通过生产环境变量配置。

当前`wrangler.jsonc`中的D1 ID是占位值。创建真实D1后替换`database_id`，再执行远程迁移。

## 迁移顺序

1. Worker健康检查和D1连接。
2. 登录、案件创建、案件读取和保存。
3. 证据元数据和R2上传。
4. 生成任务和Queue。
5. 兑换码、通知、过期清理和审计。
6. 切换前端API地址，保留FastAPI作为生成服务。

`GENERATOR_AUTH_TOKEN` 只能通过 Wrangler secret 配置，不能写入 `wrangler.jsonc` 或前端代码。
