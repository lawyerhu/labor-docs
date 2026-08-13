# OpenCode 项目交接：劳动文书助手

更新时间：2026-08-13
当前分支：`main`
交接基线提交：`5ecde47 Speed up scanned evidence generation`

## 1. 接手目标

先完成生产环境端到端生成回归，再继续优化。验收路径：使用内部测试管理员登录，打开已有测试案件，上传或沿用扫描材料，点击一次“生成正式稿”，确认进度能够跨刷新恢复，最终可下载文书和证据材料。

完成标准：

- 扫描件处理阶段不再长期停在 15%，应按文件和页数在 15%～38% 之间推进。
- 刷新页面后继续展示同一后台任务，不重复创建任务。
- 成功生成劳动仲裁申请书或民事起诉状、横向五列证据目录；有实际证据时生成连续页码证据 PDF。
- 证据名称、来源、证明目的来自模型阅读材料后的分析，不直接照抄文件名。
- 失败任务不增加生成次数，失联任务 30 分钟后自动结束。

## 2. 产品边界

网站面向劳动者和用人单位，首期覆盖：

- 劳动仲裁：仲裁申请书、证据目录、证据材料。
- 仲裁后起诉：要素式和普通式民事起诉状、证据目录、证据材料。
- 信息不足仍生成“正式稿·含待填项”；未知事实使用明确占位符，不编造事实、金额、证据、法条或案例。
- 证据目录固定为横向 A4 五列：证据编号、证据名称、来源、证明目的、页码。
- 证据 PDF 不插入“证据1/证据2”隔页，从首张实际证据页连续编码，页脚为`第X/N页`并生成书签。
- 当前公开注册暂缓，只启用一个内部测试管理员；该账号不限生成次数。密码仅存在 Cloudflare Secret，不应写入代码或文档。

模型应承担案件理解、文书撰写、证据命名和证明目的分析；程序承担金额规则、占位符、DOCX/PDF 排版、页码、存储和任务状态。

## 3. 生产架构

| 层 | 服务 | 当前用途 |
|---|---|---|
| 前端 | Netlify：`labor-docs-web.netlify.app` | Next.js 页面；从 GitHub `main` 自动部署 |
| API/数据层 | Cloudflare Worker：`labor-docs-api.fayan-research.workers.dev` | 登录、案件、D1、R2、Queue、任务进度 |
| 数据库 | Cloudflare D1：`labor-docs` | 用户、案件、证据、任务、生成物元数据 |
| 文件 | Cloudflare R2：`labor-docs-files` | 原材料、处理副本与生成物 |
| 队列 | Cloudflare Queue：`labor-docs-generation` | 异步生成任务 |
| 生成服务 | Render：`labor-docs-generator.onrender.com` | OCR、模型撰写、DOCX/PDF 排版、回写结果 |
| 模型 | OpenAI 兼容 Responses API | 案件与证据分析、正式文书撰写 |
| 法律检索 | 元典 MCP | 法条、案例、企业主体核验；失败时继续生成待核验稿 |

主链路：前端 → Worker → D1/R2/Queue → Render → R2 → Worker/D1 → 前端下载。

## 4. 当前部署状态

- GitHub `origin/main` 已指向 `5ecde47bc447ce990eb835ea7992f039fa35b30a`。
- Worker 已在 2026-08-13 15:08（北京时间）部署，健康检查正常。
- Render `/api/health` 返回 200，`generation_mode=async`。需在 Render 控制台确认当前生产部署确实来自提交 `5ecde47`；公开健康接口没有暴露 Git SHA，因此目前只验证了服务存活。
- 最新失联任务已人工结束，对应案件已恢复为 `ready_to_generate`，生成次数仍为 1。
- 仓库只有用户生成的未跟踪目录 `.codex-remote-attachments/`、`outputs/`、`work/`；保持不动，不要提交或删除。

## 5. 刚完成的性能修复

此前测试案件包含两份扫描 PDF，共 6 页。每页原始 MediaBox 为 1239×1754；旧逻辑按 2.2 倍渲染成约 2726×3859，即每页约 1050 万像素、总计约 6300 万像素。Render 免费实例只有 0.1 CPU/512 MB，Tesseract OCR 因此长期停在 15%。

提交 `5ecde47` 已做以下最小修复：

- OCR 临时渲染限制为每页最多 400 万像素；只影响 OCR 临时图，不修改原始证据或最终证据 PDF。
- OCR、文件物化、排版和产物持久化通过 `asyncio.to_thread` 执行，避免阻塞 Uvicorn 事件循环。
- OCR 按文件和页面回报进度 15%～38%。
- 成功提取的文本写入 `analysis.extracted_text`，后续生成优先读取缓存。
- 日志增加`[GENERATION-PERF]`，记录物化和提取耗时，不记录材料正文或个人信息。
- Worker 自动清理超过 30 分钟未更新的生成任务，并恢复案件状态。

代码入口：

- `apps/api/app/services/material_extraction.py`
- `apps/api/app/services/remote_generation.py`
- `apps/api/app/services/evidence_analysis.py`
- `apps/worker/src/index.ts`
- `apps/web/app/cases/[id]/page.tsx`

## 6. 首要执行步骤

### 第一步：确认 Render 生产版本

在 Render 的 `labor-docs-generator` → Events/Deploys 中确认最新部署提交为 `5ecde47`。若不是，在 GitHub 推送已成功的前提下执行 Manual Deploy → Deploy latest commit。

完成标准：Render 当前部署显示提交 `5ecde47`，`/api/health` 返回 200。

### 第二步：执行一次真实生成

使用现有测试管理员登录，刷新案件页面后只点击一次生成。观察：

- Worker D1 中 `generation_jobs.status/stage/progress/updated_at`。
- Render 日志中的`[GENERATION-PERF]`、模型调用异常和排版异常。
- 页面刷新后的任务恢复行为。

完成标准：任务进入 `completed`，生成次数只增加 1，全部输出可下载和打开。

### 第三步：根据耗时决定是否升级 Render

免费实例为 0.1 CPU/512 MB，会休眠且冷启动可能超过 50 秒。Starter 为 0.5 CPU/512 MB，当前公开价格约 7 美元/月。先记录本次各阶段实际耗时，再决定是否升级。

完成标准：有物化、OCR、模型撰写、排版和总耗时数据，决策基于测量结果。

## 7. 性能与质量约束

- 不要直接取消 400 万像素上限。它不降低最终证据 PDF 清晰度，但极小字、模糊、倾斜、低对比度页面的 OCR 准确率可能下降。
- 如需提高疑难页识别率，优先实现自适应 OCR：先用 400 万像素识别；仅在文本过少或置信度异常时提高分辨率重试该页。
- 模型撰写调用当前超时为 180 秒，位于 `apps/api/app/services/document_drafting.py`。不要仅靠无限延长超时掩盖供应商或提示词问题。
- 生成期间应持续更新进度或心跳；刷新只能恢复任务，不能创建第二个并行任务。
- 失败任务不扣次数；恢复/重试逻辑必须保持幂等。

## 8. 环境变量与 Secret

仅核对变量名和服务归属，不要把值输出到日志、提交、工单或聊天。

Render 生成服务主要变量：

- 基础：`APP_ENV`、`PORT`、`SESSION_SECRET`、`ADMIN_KEY`
- Worker 桥接：`WORKER_INTERNAL_URL`、`WORKER_INTERNAL_TOKEN`、`GENERATOR_INTERNAL_TOKEN`
- R2：`STORAGE_BACKEND=s3`、`S3_ENDPOINT_URL`、`S3_BUCKET`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`、`S3_REGION=auto`
- 模型：`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_WIRE_API=responses`
- Grok 备用模型：`GROK_BASE_URL`、`GROK_API_KEY`、`GROK_MODEL`、`GROK_WIRE_API=chat`；主模型失败后自动切换，未完整配置时不启用
- 元典：`YUANDIAN_TOKEN`，必要时覆盖三个 `YUANDIAN_*_MCP_URL`
- 邮件（恢复公开注册时才需要）：`RESEND_API_KEY`、`REGISTRATION_EMAIL_FROM`
- 测试管理员：`TEST_ADMIN_ENABLED`、`TEST_ADMIN_EMAIL`、`TEST_ADMIN_PASSWORD`

Cloudflare Worker 主要变量/Secret：

- `GENERATOR_URL`
- `GENERATOR_AUTH_TOKEN`
- `INTERNAL_API_TOKEN`
- `TEST_ADMIN_ENABLED`、`TEST_ADMIN_EMAIL`、`TEST_ADMIN_PASSWORD`

令牌配对必须一致：

- Worker `GENERATOR_AUTH_TOKEN` = Render `GENERATOR_INTERNAL_TOKEN`
- Worker `INTERNAL_API_TOKEN` = Render `WORKER_INTERNAL_TOKEN`

安全提醒：历史截图和对话中曾显示过多项生产密钥。上线前必须轮换 OpenAI、元典、Brevo/Resend、R2、SMTP 授权码、管理员密钥、会话密钥及内部令牌，并删除已停用的 SMTP 变量。不要从历史截图复制旧值。

## 9. 邮件与登录现状

- 当前目标是内部测试管理员账号密码登录，不依赖邮件验证码，且不限生成次数。
- 公开注册和普通用户密码流程暂缓，不要在功能回归完成前重新开放。
- 代码已改为 Resend HTTPS API；Brevo 账户曾返回 SMTP/API 发送权限未激活，不应作为当前依赖。
- 恢复公开注册前，需要自有域名、Resend 域名 DNS 验证、发件地址验证，并完整测试注册验证、找回密码、限流和滥用防护。

## 10. 本地验证命令

```powershell
cd C:\Users\Administrator\Documents\Codex\2026-08-11\new-chat

python -m pytest -q apps/api/tests
npm.cmd --prefix apps/worker run typecheck
npm.cmd --prefix apps/web run build
git diff --check
```

当前基线结果：API 41 项测试通过；Worker 类型检查通过；Web 构建通过。

部署 Worker：

```powershell
cd apps/worker
npm.cmd run deploy
```

查询最近生成任务：

```powershell
cd apps/worker
node_modules\.bin\wrangler.cmd d1 execute labor-docs --remote --command "SELECT id,case_id,status,stage,progress,error,created_at,updated_at FROM generation_jobs ORDER BY created_at DESC LIMIT 10"
```

## 11. 仍未完成或未充分验证

- 尚未在修复提交 `5ecde47` 上完成一次生产真实扫描件的成功生成回归。
- 本地环境没有安装 Tesseract，因此本地只验证了缩放算法和空白 PDF 进度回调，没有以真实中文扫描件测量 OCR 耗时和准确率。
- Render 免费实例仍可能因冷启动和 0.1 CPU 导致首次生成偏慢。
- 元典三类 MCP 的生产调用应分别验证法条、案例、公司查询结果，并确认失败时只标记待核验、不阻断生成。
- 正式上线前需关闭测试管理员模式、恢复并审计注册登录、兑换码、限额、删除任务和安全控制。
- README 和部分历史文档在部分 Windows 终端显示乱码；先确认文件编码和终端编码，避免无意义地重写整文件。

## 12. 修改守则

- 先复现和测量，再修改；优先小改动。
- 保留用户未跟踪目录和未提交材料。
- 原始证据永不覆盖，OCR、转换、合并仅作用于处理副本。
- 不在日志中记录身份证号、手机号、材料正文、密码或密钥。
- 不把“正式稿·含待填项”宣传为保证受理或保证胜诉。
