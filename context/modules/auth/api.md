# auth 接口详情

## 接口

| Method | Path | 权限 | 说明 |
|---|---|---|---|
| POST | `/login/access-token` | 公开 | 表单登录；返回 Bearer 兼容响应并设置会话 Cookie |
| POST | `/login/refresh` | refresh Cookie + CSRF | 轮换 refresh session，签发新 access/refresh Cookie |
| POST | `/login/logout` | refresh Cookie + CSRF | 吊销 refresh session 并清理 Cookie |
| POST | `/login/test-token` | Cookie 或 Bearer | 返回当前用户 |
| POST | `/password-recovery/{email}` | 公开 | 发送找回密码邮件，响应不暴露邮箱是否存在 |
| POST | `/reset-password/` | 公开 | 使用 reset token 更新密码 |
| POST | `/password-recovery-html-content/{email}` | 超级管理员 | 预览邮件 HTML |

登录响应仍为 `{ "access_token": "...", "token_type": "bearer" }`，用于 CLI/OpenAPI 客户端兼容。浏览器不读取或持久化响应中的 token。

## Cookie

| 名称 | Path | 有效期 | 属性 |
|---|---|---|---|
| `neomua_access` | `/api/v1` | 15 分钟 | HttpOnly, SameSite=Lax, 非 local Secure |
| `neomua_refresh` | `/api/v1/login` | 8 天 | HttpOnly, SameSite=Lax, 非 local Secure |
| `neomua_csrf` | `/` | 8 天 | SameSite=Lax, 非 local Secure，供页面复制到 Header |

refresh 数据库只保存 HMAC，不保存明文。无效、过期或重放 refresh 返回 401。Cookie mutation 的 Origin/CSRF 失败返回 403。
