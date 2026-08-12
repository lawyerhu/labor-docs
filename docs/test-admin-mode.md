# 内部测试管理员配置

当前版本暂时只开放一个内部测试账号。线上访问先经过 Cloudflare Worker，因此需要在 Worker `labor-docs-api` 的生产环境变量中配置：

| Key | Value |
|---|---|
| `TEST_ADMIN_ENABLED` | `true` |
| `TEST_ADMIN_EMAIL` | 测试管理员邮箱 |
| `TEST_ADMIN_PASSWORD` | 测试管理员密码 |

其中 `TEST_ADMIN_PASSWORD` 必须作为 Secret 保存，不要提交到 GitHub，也不要放入 Netlify 前端环境变量。`wrangler.jsonc` 已包含前两个非敏感配置；密码请在 Cloudflare Worker 控制台 **Settings → Variables and Secrets → Production → Add secret** 中添加，或在项目目录执行：

```powershell
cd apps/worker
node_modules\.bin\wrangler.cmd secret put TEST_ADMIN_PASSWORD
```

随后部署 Worker：

```powershell
cd apps/worker
npm.cmd run typecheck
npm.cmd run deploy
```

Render 的 API 服务 `labor-docs-generator` 也建议配置同名的三个变量并点击 **Save, rebuild, and deploy**，这样直连 Render 的测试也保持一致。

部署完成后，打开网站登录页，使用这组账号密码登录。管理员创建的所有案件都能生成，不受普通案件的 3 次限制，也不需要兑换码或邮箱验证码。

恢复公开注册前，将 `TEST_ADMIN_ENABLED` 改为 `false` 并重新部署；随后再恢复正式的用户注册、密码存储和找回流程。
