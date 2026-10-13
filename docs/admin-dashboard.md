# 管理后台

入口：https://laywerhu.xyz/admin

使用现有管理员邮箱和密码登录。管理员在案件工作台顶部也能看到“管理后台”入口。

Worker 使用 `ADMIN_EMAIL` 确定管理员；未配置时沿用 `TEST_ADMIN_EMAIL`。权限由服务器核对登录会话和数据库账号，不依赖前端隐藏按钮。关闭测试模式不会取消已配置管理员的后台权限；登录仍沿用现有认证流程。

后台为只读页面，提供账号统计、近 7 天新增账号、保留案件与生成次数、进行中和失败任务数，以及按邮箱搜索、分页账号列表、最近 10 个生成任务。统计接口为 `GET /api/admin/overview?page=1&search=`，未登录返回 401，非管理员返回 403，响应禁止缓存。

非管理员账号可能包含测试账号。案件、成功生成次数、最近建案和任务列表均基于当前保留记录，包含管理员的使用情况；案件删除或 30 天清理会减少这些数字，不能视为历史累计使用量。后台不展示案件正文、密码散列、生成结果或模型错误详情。

验证：

```powershell
npm --prefix apps/worker run typecheck
npm --prefix apps/worker run test:admin
npm --prefix apps/web run typecheck
npm --prefix apps/web run build
```

后台不需要数据库迁移。发布时先更新 Cloudflare Worker，再发布 Netlify 前端；现有管理员账号和密码保持不变。
