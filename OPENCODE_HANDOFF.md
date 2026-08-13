# OpenCode 项目交接：劳动文书助手

更新时间：2026-08-14
当前分支：`main`
交接基线提交：`94d8609 Bound OCR and document generation timeouts`

## 1. 接手目标

先完成生产环境端到端生成回归，再继续优化。验收路径：使用内部测试管理员登录，打开已有测试案件，上传或沿用扫描材料，点击一次“生成正式稿”，确认进度能够跨刷新恢复，最终可下载文书和证据材料。

完成标准：

- 上传后的单份材料分析不得永久停在 55%；请求 3 分钟无响应会重试，最多 3 次，最终给出明确失败状态。
- 正式稿生成不得永久停在某个进度；单次后台任务最长 10 分钟，超时后进入失败状态并允许重试。
- 刷新页面后继续展示同一后台任务，不重复创建任务。
- 成功生成劳动仲裁申请书或民事起诉状、横向四列证据目录 DOCX；有实际证据时生成连续页码证据 PDF。
- 证据名称和证明目的来自模型阅读材料后的分析，不直接照抄文件名；现行版本不要求用户填写“来源”。
- 失败任务不增加生成次数；材料任务失联 15 分钟、生成任务失联 30 分钟后自动结束。

## 2. 产品边界

网站面向劳动者和用人单位，首期覆盖：

- 劳动仲裁：仲裁申请书、证据目录、证据材料。
- 仲裁后起诉：要素式和普通式民事起诉状、证据目录、证据材料。
- 信息不足仍生成“正式稿·含待填项”；未知事实使用明确占位符，不编造事实、金额、证据、法条或案例。
- 当前证据目录为横向 A4 四列 DOCX：证据编号、证据名称、证明目的、页码。`326e147` 已按“能由模型判断就不要求用户填写”的原则移除来源列和冗余事实标题。
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
| 模型 | DeepSeek 主模型，OpenAI/Grok 备用 | 案件与证据分析、正式文书撰写；视觉复核使用支持图片的备用模型 |
| 法律检索 | 元典 MCP | 法条、案例、企业主体核验；45 秒内不可用时继续生成待核验稿 |

主链路：前端 → Worker → D1/R2/Queue → Render → R2 → Worker/D1 → 前端下载。

## 4. 当前部署状态

- GitHub `origin/main` 已指向 `94d8609`。
- `7047b1d` 的 Worker 防挂死逻辑此前已人工部署；`94d8609` 未修改 Worker。仍应通过 Worker 健康接口和一次真实任务确认生产版本。
- Render 需要确认最新部署来自 `94d8609`。只有看到该提交为 Live，下面列出的 OCR、视觉、元典和文书生成超时才会生效。
- Netlify 从 `main` 自动部署；刷新恢复任务状态的前端改动来自 `7047b1d`。
- 仓库只有用户生成的未跟踪目录 `.codex-remote-attachments/`、`outputs/`、`work/`；保持不动，不要提交或删除。

## 5. 已完成的防卡住改造

此前测试案件包含两份扫描 PDF，共 6 页。每页原始 MediaBox 为 1239×1754；旧逻辑按 2.2 倍渲染成约 2726×3859，即每页约 1050 万像素、总计约 6300 万像素。Render 免费实例只有 0.1 CPU/512 MB，Tesseract OCR 因此长期停在 15%。

提交 `5ecde47` 已完成基础性能修复：

- OCR 临时渲染限制为每页最多 400 万像素；只影响 OCR 临时图，不修改原始证据或最终证据 PDF。
- OCR、文件物化、排版和产物持久化通过 `asyncio.to_thread` 执行，避免阻塞 Uvicorn 事件循环。
- OCR 按文件和页面回报进度 15%～38%。
- 成功提取的文本写入 `analysis.extracted_text`，后续生成优先读取缓存。
- 日志增加`[GENERATION-PERF]`，记录物化和提取耗时，不记录材料正文或个人信息。
- Worker 自动清理超过 30 分钟未更新的生成任务，并恢复案件状态。

后续提交进一步补齐了完整链路：

- `2e8acc7`：原生文本/OCR 后计算疑难页风险，仅把高风险页交给视觉模型复核，再将结构化视觉结果合并回材料文本。
- `4d6c3ce`、`cfebd09`：生成前调用元典核验法律、案例及必要的公司信息，并在健康接口暴露不含密钥的配置状态。
- `7047b1d`：材料分析请求 3 分钟超时、最多重试 3 次；15 分钟失联材料自动失败；前端刷新后恢复正在处理的材料和生成任务。
- `94d8609`：材料提取总计 5 分钟、单页 OCR 30 秒、单个视觉页 45 秒、元典整组查询 45 秒、单个文书模型供应商 90 秒、整个生成任务 10 分钟。进度回调最多等待 5 秒，不能反过来拖住已完成的 OCR。
- 视觉复核每份材料最多 3 个最高风险页面；单个视觉供应商 25 秒且只尝试 1 次，失败后保留 OCR/原生文本继续生成。
- 证据命名和证明目的模型调用 30 秒且只尝试 1 次；失败时保留提取文本，使用确定性名称和`[待核实证明目的]`继续流程，而不是让材料永远停在 55%。
- DeepSeek 负责最终案情分析和文书撰写；图片只由支持视觉的 OpenAI/Grok 备用模型读取，视觉结果以结构化文本传递给 DeepSeek。

代码入口：

- `apps/api/app/services/material_extraction.py`
- `apps/api/app/services/remote_generation.py`
- `apps/api/app/services/evidence_analysis.py`
- `apps/worker/src/index.ts`
- `apps/web/app/cases/[id]/page.tsx`

## 6. 首要执行步骤

### 第一步：确认三个生产版本

在 Render 的 `labor-docs-generator` → Events/Deploys 中确认最新部署提交为 `94d8609`。若不是，执行 Manual Deploy → Deploy latest commit。随后确认 Netlify 最新生产部署来自同一条 `main` 历史，并调用 Worker、Render 的健康接口。

完成标准：Render 显示 `94d8609` 为 Live，Render `/api/health` 返回 200 且模型/元典配置布尔值符合预期，Worker `/api/health` 返回 200。

### 第二步：执行一次真实生成

使用现有测试管理员新建案件，上传两份材料，等每份材料都进入 ready/失败终态后只点击一次生成。观察：

- Worker D1 中证据的`status/processing_stage/processing_progress`及`generation_jobs.status/stage/progress/updated_at`。
- Render 日志中的`[GENERATION-PERF]`、`[OCR-SKIPPED]`、`[VISION-REVIEW-SKIPPED]`、模型调用异常和排版异常。
- 页面刷新后的任务恢复行为。

完成标准：任务进入 `completed`，生成次数只增加 1，全部输出可下载和打开。

### 第三步：验证超时与降级

至少做一次受控失败测试：临时使用无法响应的测试模型地址，或上传一份难识别但不含敏感信息的扫描样例，确认任务最终失败或降级完成，页面不再永久显示“正在阅读材料/正在生成”。测试后立即恢复生产配置。

完成标准：材料分析最晚经 3 次重试进入明确终态；正式生成最晚 10 分钟进入明确终态；刷新后仍能看到同一任务及最终结果。

### 第四步：根据耗时决定是否升级 Render

免费实例为 0.1 CPU/512 MB，会休眠且冷启动可能超过 50 秒。Starter 为 0.5 CPU/512 MB，当前公开价格约 7 美元/月。先记录本次各阶段实际耗时，再决定是否升级。

完成标准：有物化、OCR、模型撰写、排版和总耗时数据，决策基于测量结果。

## 7. 性能与质量约束

- 不要直接取消 400 万像素上限。它不降低最终证据 PDF 清晰度，但极小字、模糊、倾斜、低对比度页面的 OCR 准确率可能下降。
- 如需提高疑难页识别率，优先实现自适应 OCR：先用 400 万像素识别；仅在文本过少或置信度异常时提高分辨率重试该页。
- 单个文书模型供应商当前超时为 90 秒且只尝试一次，位于 `apps/api/app/services/document_drafting.py`；供应商失败后会切换备用模型。
- 生成期间应持续更新进度或心跳；刷新只能恢复任务，不能创建第二个并行任务。
- 失败任务不扣次数；恢复/重试逻辑必须保持幂等。
- 视觉模型最多复核 3 个高风险页，其余页面使用原生文本/OCR。这是防止低配实例被大量视觉请求拖死的有意限制。
- Worker 材料分析请求上限为 3 分钟，而生成服务材料提取内部上限为 5 分钟。若生产中经常在 3 分钟被 Worker 中止，应先测量真实耗时，再统一预算或拆成真正的后台材料任务，避免简单延长两边超时。

## 8. 环境变量与 Secret

仅核对变量名和服务归属，不要把值输出到日志、提交、工单或聊天。

Render 生成服务主要变量：

- 基础：`APP_ENV`、`PORT`、`SESSION_SECRET`、`ADMIN_KEY`
- Worker 桥接：`WORKER_INTERNAL_URL`、`WORKER_INTERNAL_TOKEN`、`GENERATOR_INTERNAL_TOKEN`
- R2：`STORAGE_BACKEND=s3`、`S3_ENDPOINT_URL`、`S3_BUCKET`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`、`S3_REGION=auto`
- DeepSeek 主模型：`DEEPSEEK_BASE_URL=https://api.deepseek.com`、`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL=deepseek-v4-flash`、`DEEPSEEK_WIRE_API=chat`、`DEEPSEEK_REASONING_EFFORT=high`
- OpenAI 第一备用模型/第一视觉模型：`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_WIRE_API=responses`，需要时设置 `OPENAI_REASONING_EFFORT`。当前预期模型为 GPT-5.6 Luna Max，以 Render 实际变量为准。
- Grok 第二备用模型：`GROK_BASE_URL`、`GROK_API_KEY`、`GROK_MODEL=grok-4.6`、`GROK_WIRE_API=chat`、`GROK_REASONING_EFFORT=high`
- 模型按 DeepSeek → OpenAI → Grok 顺序自动切换；任一组变量未完整配置时跳过该模型
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

`94d8609` 的针对性回归结果：

```text
python -m pytest tests/test_material_extraction.py tests/test_evidence_analysis.py tests/test_remote_generation.py tests/test_document_drafting.py
13 passed
python -m compileall -q app
通过
```

此前全量基线：API 41 项测试通过；Worker 类型检查通过；Web 构建通过。更新依赖或公共接口后应重新跑全量命令。

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

- 尚未在 `94d8609` 上完成一次生产真实扫描件从上传、视觉复核、元典核验到文书下载的完整成功回归。
- 本地环境没有安装 Tesseract，因此本地只验证了缩放算法和空白 PDF 进度回调，没有以真实中文扫描件测量 OCR 耗时和准确率。
- Render 免费实例仍可能因冷启动和 0.1 CPU 导致首次生成偏慢。
- 元典三类 MCP 的生产调用应分别验证法条、案例、公司查询结果，并确认超过 45 秒时只标记待核验、不阻断生成。
- `asyncio.wait_for(asyncio.to_thread(...))`只能停止等待，不能强制终止任意已经卡死的 Python 线程。Tesseract 单页已有子进程超时，但若将来发现 PDF 渲染或 Office 转换本身长期卡死，应把该步骤放到可终止的独立子进程，而不是继续增加线程和超时。
- 进度回调现在并发刷新且最多等待 5 秒，但仍会为页面进度创建多个 future；超大文件如出现内存增长，应改为合并/节流“最新进度”，不要恢复逐页同步 HTTP 回调。
- 旧部署已创建的在途任务不会自动获得新代码语义。验证 `94d8609` 时优先新建案件；旧任务按 15/30 分钟失联规则收敛。
- 正式上线前需关闭测试管理员模式、恢复并审计注册登录、兑换码、限额、删除任务和安全控制。
- README 和部分历史文档在部分 Windows 终端显示乱码；先确认文件编码和终端编码，避免无意义地重写整文件。

## 12. 修改守则

- 先复现和测量，再修改；优先小改动。
- 保留用户未跟踪目录和未提交材料。
- 原始证据永不覆盖，OCR、转换、合并仅作用于处理副本。
- 不在日志中记录身份证号、手机号、材料正文、密码或密钥。
- 不把“正式稿·含待填项”宣传为保证受理或保证胜诉。
