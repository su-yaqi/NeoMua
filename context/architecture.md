# 系统架构

## 整体分层

NeoMua 采用典型前后端分离单体架构：

```text
React SPA
  -> generated OpenAPI client / custom tenantApi
  -> FastAPI routes + dependency guards
  -> CRUD / SQLModel
  -> PostgreSQL
```

## 模块划分与依赖

```text
auth -------> users
users ------> namespaces
items ------> users
namespaces --> users
llm_configs -> namespaces
llm_configs -> users

frontend routes --> frontend components --> client SDK / tenantApi
backend routes --> deps / crud --> models --> db
```

### 边界说明

- `auth` 负责会话建立与凭证校验，不拥有独立表结构，依赖 `users` 读写用户账号。
- `users` 负责账号生命周期与平台级用户管理，是 `items` 与 `namespaces` 的上游模块。
- `items` 只管理归属到 `owner_id` 的个人条目，不感知空间维度。
- `namespaces` 负责空间实体、用户-空间关联关系和空间管理员权限校验。
- `llm_configs` 负责空间级供应商接入配置、密钥加密存储、供应商目录、连接校验和模型集合维护，运行时调用暂不在本期范围内。

## 关键架构决策

- FastAPI + SQLModel：沿用模板生态，降低脚手架维护成本。
- OpenAPI 生成客户端：前端对标准接口使用自动生成 SDK，减少手写类型漂移。
- `tenantApi` 独立 Axios 实例：为命名空间扩展保留自定义 Header 注入能力。
- `X-Namespace-Id` + `require_namespace_admin`：通过请求头或查询参数绑定当前空间上下文。
- 供应商预置目录内置在后端服务层：以统一 `ProviderDefinition` 描述不同供应商的接入参数、鉴权方式、探活和模型发现能力。
- 密钥仅以加密密文落库、对外只返回掩码：通过 `SECRET_KEY` 派生密钥流和签名校验，避免明文回传。
- 单体服务 + Compose 编排：当前规模下优先简化开发、测试和部署链路。

## 部署拓扑

```text
browser
  -> frontend (Vite build / Nginx)
  -> backend (FastAPI)
  -> db (PostgreSQL)

Adminer 作为数据库管理工具挂在同一 Compose 拓扑中。
Traefik 负责域名路由与 HTTPS 终止。
prestart 容器负责迁移前准备与初始化检查。
```

## 非功能性约束
| 类型 | 要求 |
|------|------|
| 安全 | JWT Bearer Token 鉴权；密码使用 Argon2/Bcrypt 兼容校验；重置密码接口避免邮箱枚举 |
| 可维护性 | 前后端均基于模板标准目录；接口类型由 OpenAPI 生成；文档需同步到 `context/` |
| 部署 | 所有核心服务均以容器方式运行，依赖 `.env` 注入配置 |
| 测试 | 后端路由与 CRUD 有 Pytest，前端关键页面有 Playwright 覆盖 |
