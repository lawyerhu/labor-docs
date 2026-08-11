# 预发布安全与隐私检查

## 已实现

- 生产环境拒绝默认 `SESSION_SECRET` 和 `ADMIN_KEY`。
- 登录验证码单个验证码最多错误尝试 5 次；验证码仍有 10 分钟有效期和请求频率限制。
- 生产 Worker 的内部接口要求 `INTERNAL_API_TOKEN`；本地免鉴权仅限 `ENVIRONMENT=local`。
- 生成服务只接收 `job_id`、`case_id`，案件正文通过受保护接口读取，不进入 Queue 消息。
- 上传文件检查真实容器、扩展名、ZIP 解压规模和可选 ClamAV 扫描。
- 生成错误在生产响应和 Celery 结果中不返回本地路径或异常正文。
- FastAPI 和 Worker 均提供到期清理；清理对象存储文件、案件正文、证据、文书、任务和通知元数据。
- `.wrangler`、环境文件和运行时数据库不进入版本控制。

## 上线前必须人工确认

- 将 `SESSION_SECRET`、`ADMIN_KEY`、`GENERATOR_INTERNAL_TOKEN`、`WORKER_INTERNAL_TOKEN` 和 `BREVO_API_KEY` 写入部署平台 Secret，不写入代码或普通变量。
- R2 使用实际桶、S3 密钥最小权限和短期下载 URL；验证 Worker 与生成服务之间的网络来源限制。
- 配置邮件服务、ClamAV 病毒库、HTTPS、限流/WAF、备份清理和告警。
- 用真实 D1/R2/Queue 预发布资源执行一次：上传材料 → 生成 → 下载 → 30 天清理。
- 检查日志和审计系统不记录身份证号、手机号、案件正文、原始文件和内部令牌。
- 完成越权访问、批量注册、提示词注入、文件上传、队列重试和跨案件对象键访问测试。
