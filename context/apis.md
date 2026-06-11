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

### utils / private
| Method | Path | 描述 |
|--------|------|------|
| GET | /utils/health-check/ | 健康检查 |
| POST | /utils/test-email/ | 超级管理员发送测试邮件 |
| POST | /private/users/ | 仅本地环境可用的测试建用户接口 |
