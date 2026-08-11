# 劳动文书助手

面向劳动者和用人单位的劳动仲裁、仲裁后起诉文书生成网站。系统允许案件信息不完整时生成带明确占位符的正式格式稿；不会编造事实、证据或未经核验的法律依据。

## 已实现的首版能力

- 邮箱验证码登录（开发环境直接返回验证码，生产环境需接邮件服务）。
- 首案免费、后续案件兑换码解锁、每案最多成功生成3次。
- 案情与诉求输入、大模型分析、单轮集中追问和证据上传的线性流程。
- PDF、图片、Word、Excel材料上传；原始副本不修改。
- 仲裁申请书或民事起诉状 DOCX；身份和送达信息不在线收集，统一保留明确待填项。
- 横向A4五列证据目录 PDF；无证据时只有表头，不生成空白证据材料 PDF。
- 有证据时按目录顺序合并，连续标注`第X/N页`并建立书签。
- 30天案件生命周期、7/3/1天站内提醒和到期清理任务（清理案件正文与文件）。
- OpenAI兼容分析适配器；元典法条、案例和企业 MCP 核验；S3兼容对象存储和 Docker 部署文件。
- Cloudflare Worker/D1/R2/Queues 数据层骨架，以及 FastAPI 生成服务的内部桥接契约。

## 本地开发

### 后端

```powershell
cd apps/api
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

默认使用当前目录下的 SQLite 和本地 `storage` 文件夹。

### 前端

```powershell
cd apps/web
npm install
npm run dev
```

打开 `http://localhost:3000`。Next.js 会把 `/api` 转发到 `http://127.0.0.1:8000`。

### 测试

```powershell
cd apps/api
python -m pytest

cd ../web
npm run typecheck
npm run build
```

## Docker

复制 `.env.example` 为 `.env`，至少替换 `SESSION_SECRET`、`ADMIN_KEY` 和数据库密码，然后执行：

```powershell
docker compose up --build
```

配置真实域名到 `SITE_ADDRESS` 后，Caddy 自动申请和续期 HTTPS 证书。

## 上线前仍需接入

- 邮件服务商：把开发环境返回验证码改为真实邮件发送。
- 对象存储：开发环境默认使用本地卷；生产环境配置`STORAGE_BACKEND=s3`及S3兼容参数。
- Cloudflare生产桥接：配置R2 S3参数、Worker内部地址及生成服务内部令牌；完成端到端部署验证后再切换前端生成入口。
- 元典：生产环境配置`YUANDIAN_TOKEN`；调用失败不阻断生成，未核验内容保持待核验状态。
- ClamAV病毒库更新、短信/设备风控和运营告警；生产环境确认扫描服务可用后设置`ANTIVIRUS_REQUIRED=true`。
- 正式商用前完成模板、字体、技能和第三方依赖许可审计。
