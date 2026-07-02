# 接口总览

> 本项目后端统一前缀为 `/api/v1`，接口详细说明见各模块 `modules/*/api.md`。

## 全局规范

### 基础约定

- Base URL：`/api/v1`
- 协议：开发环境支持 HTTP，本地/生产通常由 Traefik 统一接入
- 请求格式：JSON 为主，登录接口使用 OAuth2 表单
- 认证方式：`Authorization: Bearer <token>`

### 响应风格

当前项目没有强制统一的最外层响应包裹，主要分为以下几类：

- 列表接口：`{ "data": [...], "count": number }`
- 消息接口：`{ "message": "..." }`
- 鉴权接口：`{ "access_token": "...", "token_type": "bearer" }`
- 详情接口：直接返回单个实体对象
- 健康检查：直接返回 `true`

### 错误处理约定

- FastAPI 默认错误格式：`{ "detail": "..." }`
- 常见状态码：

| HTTP 状态码 | 触发场景 |
|------------|---------|
| 400 | 登录失败、密码错误、参数缺失、无命名空间上下文 |
| 401/403 | 未登录或权限不足 |
| 404 | 资源不存在 |
| 409 | 唯一键冲突，如邮箱/空间编码重复 |

### 命名空间上下文

- 部分空间相关接口依赖 `X-Namespace-Id` Header。
- `require_namespace_admin` 会校验当前用户在目标空间中是否具有 `admin` 角色。
- 超级管理员可绕过普通空间角色限制。

## 接口目录

### auth
| Method | Path | 描述 |
|--------|------|------|
| POST | /login/access-token | 登录并签发 JWT |
| POST | /login/test-token | 校验当前 Token |
| POST | /password-recovery/{email} | 发送找回密码邮件 |
| POST | /reset-password/ | 使用 token 重置密码 |
| POST | /password-recovery-html-content/{email} | 超级管理员预览找回密码邮件 |

### users
| Method | Path | 描述 |
|--------|------|------|
| GET | /users | 超级管理员分页读取用户 |
| POST | /users | 超级管理员创建用户 |
| GET | /users/me | 读取当前用户 |
| PATCH | /users/me | 修改当前用户资料 |
| PATCH | /users/me/password | 修改当前用户密码 |
| DELETE | /users/me | 当前用户自助注销 |
| POST | /users/signup | 公开注册 |
| GET | /users/{user_id} | 读取指定用户 |
| PATCH | /users/{user_id} | 超级管理员更新用户 |
| DELETE | /users/{user_id} | 超级管理员删除用户 |

### items
| Method | Path | 描述 |
|--------|------|------|
| GET | /items | 读取条目列表 |
| GET | /items/{id} | 读取单个条目 |
| POST | /items | 创建条目 |
| PUT | /items/{id} | 更新条目 |
| DELETE | /items/{id} | 删除条目 |

### namespaces
| Method | Path | 描述 |
|--------|------|------|
| GET | /namespaces/mine | 读取当前用户可见空间 |
| GET | /namespaces/{namespace_id}/users | 读取空间成员 |
| POST | /namespaces/{namespace_id}/users | 向空间新增成员 |
| PATCH | /namespaces/{namespace_id}/users/{user_id} | 更新空间成员 |
| DELETE | /namespaces/{namespace_id}/users/{user_id} | 移除空间成员 |

### platform
| Method | Path | 描述 |
|--------|------|------|
| GET | /platform/namespaces | 超级管理员读取空间列表 |
| POST | /platform/namespaces | 超级管理员创建空间 |
| PATCH | /platform/namespaces/{namespace_id} | 超级管理员更新空间 |
| DELETE | /platform/namespaces/{namespace_id} | 超级管理员删除空间 |
| GET | /platform/users | 超级管理员读取平台用户列表 |
| POST | /platform/users | 超级管理员创建平台用户并分配空间 |
| PATCH | /platform/users/{user_id} | 超级管理员更新平台用户 |
| DELETE | /platform/users/{user_id} | 超级管理员删除平台用户 |

### llm
| Method | Path | 描述 |
|--------|------|------|
| GET | /llm/providers/catalog | 读取预置供应商目录 |
| GET | /llm/provider-configs | 读取当前空间的大模型接入配置列表 |
| POST | /llm/provider-configs | 创建当前空间的大模型接入配置 |
| PATCH | /llm/provider-configs/{config_id} | 更新当前空间的接入配置 |
| POST | /llm/provider-configs/{config_id}/validate | 对当前配置执行连接校验 |
| POST | /llm/provider-configs/{config_id}/sync-models | 拉取供应商模型并合并手工模型 |

### utils / private
| Method | Path | 描述 |
|--------|------|------|
| GET | /utils/health-check/ | 健康检查 |
| POST | /utils/test-email/ | 超级管理员发送测试邮件 |
| POST | /private/users/ | 仅本地环境可用的测试建用户接口 |

### runtime management

| Method | Path | 权限与用途 |
|---|---|---|
| GET/PUT | `/runtimes/platform` | admin 配置；admin/developer 读取空间平台运行时 |
| POST | `/runtimes/platform/sessions` | admin/developer 创建多轮测试会话 |
| POST | `/runtimes/sessions/{id}/messages` | admin/developer 提交会话消息 |
| GET | `/runtimes/tasks/{id}/events` | 读取完整持久事件 |
| GET | `/runtimes/tasks/{id}/stream` | 支持 Last-Event-ID 的鉴权 SSE |
| POST/GET | `/runtimes/nodes/enrollment-tokens` | admin 创建一次性令牌/读取无明文列表 |
| POST | `/node/enroll` | 节点使用一次性令牌和 Ed25519 公钥注册 |
| WS | `/node/ws` | 设备 JWT + 时间戳签名鉴权的 WSS 心跳、任务和内容通道 |
| GET/PUT | `/runtimes/nodes[/{id}/runtime]` | admin/developer 列表；admin 配置节点模型/API |
| DELETE | `/runtimes/nodes/{id}/credential` | admin 吊销节点及全部凭证 |
| POST/GET | `/runtime-tasks` | admin/developer 下发普通任务；admin 才可下发管理任务 |
| POST | `/runtime-tasks/{id}/cancel|retry` | 显式取消或新建 retry 任务，要求幂等键 |
| POST/GET | `/runtime-artifacts` | admin 流式上传；admin/developer 读取不可变制品 |
| POST | `/runtime-artifacts/releases` | admin 创建有时效的节点发布 |
| POST | `/runtime-artifacts/deployments/{id}/retry|rollback` | admin 显式重试或回滚 |
| GET | `/node/artifacts/{id}/download` | 节点凭短期、部署范围 JWT 下载 |

内部 `/internal/runtime/*` 仅接受独立服务凭证；浏览器 JWT 无法访问。Model Gateway token 绑定 namespace/runtime/task/model，不能换模型或跨空间使用。
