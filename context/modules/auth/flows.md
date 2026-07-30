# auth 业务流程

## 登录、刷新与退出

1. 浏览器以 OAuth2 表单调用 `/login/access-token`。
2. 后端校验用户后返回兼容 Bearer 客户端的 access token，同时设置三个 host-only Cookie：15 分钟 HttpOnly access、8 天 HttpOnly refresh、页面可读的 CSRF token。
3. SPA 不保存 access/refresh token；通过携带 Cookie 的 `/users/me` 判断登录态。
4. access 过期出现 401 时，浏览器只发起一个受控 `/login/refresh` 请求；refresh token 每次使用后立即轮换，原请求只重试一次。
5. 已轮换 refresh token 被重放时，后端吊销同一 family 的全部 refresh session。
6. 退出调用 `/login/logout`，服务端吊销 refresh session 并清理三个 Cookie。

非浏览器集成继续使用 `Authorization: Bearer <access token>`，不依赖 Cookie 或 CSRF。

## CSRF

- Cookie 鉴权的 POST/PUT/PATCH/DELETE 必须同时满足：`Origin` 在配置的前端 origin 中，且 `X-CSRF-Token` 与 CSRF Cookie 一致。
- Bearer、内部服务凭证、节点注册和公开认证入口不套用 Cookie CSRF 规则。
- Cookie 为 host-only；非 local 环境设置 `Secure`，统一 `SameSite=Lax`。

## 找回密码

找回密码对存在与不存在邮箱返回相同结果。重置 token 验证成功且用户仍启用时才更新密码。

## E2E 登录隔离

Playwright 创建临时用户后通过真实登录页建立 Cookie `storageState`；测试不写入 `localStorage.access_token`，固定开发账号不属于自动化链路。
