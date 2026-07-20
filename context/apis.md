# 接口总览

> 本项目后端统一前缀为 `/api/v1`，接口详细说明见各模块 `modules/*/api.md`。

## 全局规范

### 基础约定

- Base URL：`/api/v1`
- 协议：开发环境支持 HTTP，本地/生产通常由 Traefik 统一接入
- 请求格式：JSON 为主，登录接口使用 OAuth2 表单
- 认证方式：浏览器使用 HttpOnly Cookie 会话；CLI/API 使用 `Authorization: Bearer <token>`
- 浏览器请求统一携带 credentials；Cookie 鉴权的状态修改请求还必须发送 `X-CSRF-Token` 并通过 Origin 校验

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
| POST | /login/refresh | 轮换浏览器 refresh session |
| POST | /login/logout | 吊销浏览器会话并清理 Cookie |
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
| GET | /llm/provider-configs/{config_id}/runtime-readiness | 查看 Worker 发行、持久协调、逐模型真实调用与最终 Binding 就绪状态 |
| POST | /llm/provider-configs/{config_id}/sync-models | 拉取供应商模型并合并手工模型 |
| POST | /llm/provider-configs/draft/validate | 使用未保存的地址、密钥和扩展参数校验连接 |
| POST | /llm/provider-configs/draft/sync-models | 使用未保存配置同步模型预览，不产生数据库实体 |

### utils / private
| Method | Path | 描述 |
|--------|------|------|
| GET | /utils/health-check/ | 健康检查 |
| POST | /utils/test-email/ | 超级管理员发送测试邮件 |
| POST | /private/users/ | 仅本地环境可用的测试建用户接口 |

### runtime management

| Method | Path | 权限与用途 |
|---|---|---|
| GET | `/runtimes` | admin/developer 读取平台内置、服务节点受管和客户端发现的 Runtime Instance |
| GET | `/runtimes/{id}` | 返回实例、当前配置、能力报告、模型目录与绑定摘要 |
| PUT | `/runtimes/{id}/configuration` | 历史手工 Runtime 可写；v0.10 三类系统管理 Runtime 拒绝用户配置 |
| POST | `/runtimes/{id}/configuration/apply` | admin 请求应用 desired 配置 |
| POST | `/runtimes/{id}/model-bindings` | admin 声明稳定模型在该 Runtime 上的精确路由 |
| POST | `/runtimes/{id}/model-bindings/{binding_id}/validate` | admin 发起 provider 或 runtime-native 模型验证 |
| GET/POST | `/llm/model-definitions` | 读取稳定模型目录；admin 对明确模型身份建档 |
| GET | `/runtimes/tasks/{id}/events` | 读取完整持久事件 |
| GET | `/runtimes/tasks/{id}/stream` | 支持 Last-Event-ID 的鉴权 SSE |
| POST | `/runtimes/nodes/bootstrap-sessions` | admin 创建绑定 service/client 模式且只显示一次的 bootstrap 凭证 |
| GET | `/runtimes/nodes/bootstrap-sessions` | admin 查看 bootstrap 阶段、绑定节点、发行与完成状态；不再返回密文 |
| POST/GET | `/runtime-node-distributions/adapters` | 超级管理员发布/读取不可变签名 Claude Code/Codex Adapter release |
| POST/GET | `/runtime-node-distributions` | 超级管理员发布/读取签名 Linux/systemd Node 发行 ZIP 与逐文件清单 |
| POST | `/node/bootstrap/preflight` | 安装器绑定设备公钥、校验主机并取得精确签名发行清单 |
| GET | `/node/bootstrap/distributions/{release_id}` | bootstrap 凭证范围内下载精确不可变发行 ZIP |
| POST | `/node/bootstrap/receipt` | 节点提交设备签名的发行/组件安装 receipt |
| POST | `/node/bootstrap/stage` | 节点追加 service 激活或安装失败 attempt |
| POST | `/node/bootstrap/recover` | 仅在 enrollment 响应丢失时，以同一设备密钥证明恢复凭证 |
| POST | `/node/enroll` | 节点使用一次性令牌和 Ed25519 公钥注册 |
| WS | `/node/ws` | 设备 JWT + 时间戳签名鉴权的 WSS 心跳、任务和内容通道 |
| GET | `/runtimes/nodes` | admin/developer 读取机器节点及其 Runtime Instance 摘要 |
| POST | `/runtime-nodes/{id}/discovery/refresh` | admin 请求 client Node 下一代受控有限探测 |
| GET | `/runtime-nodes/{id}/discovery-observations` | admin/developer 查看逐代摘要和脱敏诊断 |
| POST | `/runtimes/{id}/pause|resume` | admin 审计式暂停/恢复 service/client 新任务调度；恢复重新核验证据 |
| DELETE | `/runtimes/nodes/{id}/credential` | admin 吊销节点及全部凭证 |
| POST/GET | `/runtime-tasks` | admin/developer 下发普通任务；admin 才可下发管理任务 |
| POST | `/runtime-tasks/{id}/cancel|retry` | 显式取消或新建 retry 任务，要求幂等键 |
| POST/GET | `/runtime-artifacts` | admin 流式上传；admin/developer 读取不可变制品 |
| POST | `/runtime-artifacts/releases` | admin 创建有时效的节点发布 |
| POST | `/runtime-artifacts/deployments/{id}/retry|rollback` | admin 显式重试或回滚 |
| GET | `/node/artifacts/{id}/download` | 节点凭短期、部署范围 JWT 下载 |

旧 `/runtimes/platform` 创建、节点 Runtime Profile 与 Harness 写接口仅保留只读/410 语义。平台内置 Runtime 由 namespace 初始化和目录读取幂等创建；service/client Runtime 只由设备身份通道的发现报告创建。内部 `/internal/runtime/*` 仅接受独立服务凭证；浏览器 JWT 无法访问。

### agent management（v0.5-v0.9）

| 资源 | 主要正式接口 |
|---|---|
| Agent | `/agents`、`/agents/{id}/draft`、`/agents/{id}/draft/validate`；v0.9 草稿使用稳定模型偏好和引擎中立策略，Harness 只读 |
| Skill/Tool | `/skills`、`/skills/complete/editor`、`/skills/{id}/draft[/files]`、`/skills/{id}/draft/validate|publish|import`、`/skills/{id}/current-version`、`/skills/{id}/versions`、`/tools/catalog`、`/tools/{key}/namespace-policy`、`/agents/{id}/draft/skills|tools` |
| MCP | `/mcp-servers[/{id}/revisions]`、`/mcp-revisions/{id}/targets`、`/mcp-targets/{id}/secret|validate|validations|runtime` |
| Plugin | `/plugins[/{id}/draft|versions]`、`/agents/{id}/draft/plugins` |
| Release/Activation | `/agents/{id}/releases`、`/agent-releases/{id}`、`/agent-releases/{id}/activations/precheck|activations`、`/agent-deployments/{id}/retry|rollback` |
| Runtime Agent/Approval | `/runtime-agents`、`/runtime-tasks/{id}/approvals`、`/tool-approvals/{id}/approve|deny` |
| Operator CLI | `/cli/login|refresh|logout`、`/capabilities`；其余命令复用以上正式接口 |

发布、激活、Plugin Version、retry/rollback 和任务创建使用 `Idempotency-Key`。MCP/Release Worker 领取与回传继续位于受独立服务凭证保护的 `/internal/runtime/*`；节点结果经已鉴权 WSS 转发。读接口不返回 secret value。

v0.9 Activation precheck 按 Runtime Instance 的 applied 配置、在线且未过期的能力报告、模型目录、Skill 和 MCP 状态生成短时证据；Task 创建冻结 exact model binding 或唯一解析的 Agent 偏好。`GET /agent-releases/{release_id}/compatibility` 返回正式兼容性诊断；任务事件会按调用序号写入 `agent_task_model_call_usage`，任务详情返回 binding、usage、状态和脱敏错误证据。

v0.7 为 Agent、Skill、MCP 与 Plugin 增加 complete-create 接口，由一次请求原子创建身份与必要初始草稿/版本/Revision；失败不遗留只有名称和标识的半成品。用户输入字段仍使用稳定的 `slug` API 名称，但 UI 展示为“唯一标识”。

v0.8 的 `POST /skills/complete/editor` 原子创建 Skill 身份、草稿与初始文件；旧 ZIP complete-create 继续兼容，但后续版本统一进入草稿工作台。草稿文件支持创建、读取、更新、移动和删除，任何内容变更推进 revision 并使旧校验失效；只有当前 revision 校验通过后才能发布。发布和 `POST /skills/{id}/current-version` 以 `Idempotency-Key` 原子推进 `current_version_id` 并保留切换审计。已发布版本只读，可浏览文件或 deprecate，不能覆盖内容。

`PUT /agents/{id}/draft/skills` 与 Plugin 草稿中的 Skill contribution 只提交 Skill identity 和 enabled 状态，不接受版本锁定；Release Resolved Spec 同样只携带 Skill 身份。

### Skill Runtime 同步（v0.8）

| Method | Path | 权限与用途 |
|---|---|---|
| GET | `/skills/{skill_id}/runtime-sync` | admin/developer 查看一个 Skill 在各 Runtime 的 desired/applied 矩阵 |
| GET | `/runtimes/{runtime_id}/skills` | admin/developer 查看一个 Runtime 的全部 Skill 同步状态 |
| POST | `/runtime-skill-states/{state_id}/retry` | admin 显式重试失败状态 |
| POST | `/skills/{skill_id}/runtime-sync/{runtime_id}/retry` | admin 按 Skill/Runtime 目标显式重试 |
| POST | `/internal/runtime/skill-sync/claim` | 平台 Worker 在任务循环之外领取待同步目标 |
| GET | `/internal/runtime/skill-sync/{attempt_id}/download` | 平台 Worker 使用服务凭证下载目标签名 Bundle |
| POST | `/internal/runtime/skill-sync/{attempt_id}/result` | 平台 Worker 回传 verified/committed/failed 两阶段结果 |
| GET | `/node/skill-sync/{attempt_id}/download` | 节点凭目标绑定短期令牌下载 Bundle |
| POST | `/internal/runtime/skill-sync/tasks/{task_id}/usage` | Runtime 上报单次任务实际使用的本地 Skill 证据 |
| POST | `/internal/runtime/skill-sync/tasks/{task_id}/preparation-failed` | Runtime 上报缺失或损坏缓存并阻断任务准备 |

节点侧同步通知、desired 请求、结果和 commit 通过设备鉴权 WSS 消息完成。同步与任务使用互不调用：任务领取只核对本地 applied 缓存并创建任务级只读绑定，不在热路径下载、解包或重新解析 Skill。

### project management（v0.6）

| 资源 | 主要接口 |
|---|---|
| 项目/成员 | `GET/POST /projects`、`GET/PATCH /projects/{id}`、`GET/PUT /projects/{id}/members` |
| 仓库 | `/projects/{id}/repositories`、`POST /projects/{id}/repositories/{repo_id}/validate` |
| Spec 位置/绑定 | `/projects/{id}/spec-locations`、`PUT .../{location_id}/binding`、`POST .../{location_id}/diff` |
| Spec 标准 | `/spec-standards`、`/spec-standards/{id}/versions`、`PATCH /spec-standard-versions/{id}` |

配置 mutation 仅 namespace Admin/Developer 可用。仓库验证只接受 Runtime 已上报的 workspace ref 与 commit，不在控制面匿名 clone 或猜测结果。

`POST /projects/complete` 可在一次事务中创建项目、默认 Runtime Instance、初始成员及可选仓库；`POST /spec-standards/complete` 同时创建标准身份与第一个不可变版本，避免不完整顶层实体。

### conversation management（v0.6-v0.9）

| 资源 | 主要接口 |
|---|---|
| 可用目录 | `/conversation-catalog/runtimes|models|agents`，模型目录返回具体 Runtime Model Binding |
| 会话 | `GET/POST /conversations`、`GET/PATCH /conversations/{id}`、`POST /conversations/{id}/derive` |
| 配置修订 | `POST /conversations/{id}/configuration-revisions`，使用 `expected_revision` 更新模型或 Agent 参与配置 |
| 消息/圆桌 | `GET/POST /conversations/{id}/messages`、`GET/POST /conversations/{id}/delegations` |
| 附件 | `GET/POST /conversations/{id}/attachments`（严格扫描 UTF-8 文本、Markdown、JSON） |
| 项目上下文 | `POST /conversations/{id}/context-snapshots` |

创建、派生、消息与委派使用 `Idempotency-Key`。项目与 Runtime Instance 创建后固定；Chat 使用 exact binding，Agent 参与者可使用 exact 或 `agent_preference`，保存时冻结解析证据。参与者以 `conversation_agent_id` 作为角色实例身份，同一 Agent/Release 可以出现于多个独立角色，但每个角色保存独立绑定和模型证据。

### workflow management（v0.6）

| 资源 | 主要接口 |
|---|---|
| 模板 | `/workflow-templates`、`/{id}/versions/{version_id}`、`/{id}/enablement`、`/{id}/preflight` |
| 模板执行配置 | `GET/PUT /workflow-templates/{id}/versions/{version_id}/execution-configuration` |
| 项目任务 | `GET/POST /projects/{id}/workflow-instances`、`GET /workflow-instances/{id}`、`POST /workflow-instances/{id}/cancel` |
| 用户侧实例 | `GET/POST /workflow-instances`，可按 `template_id`/`template_version_id` 筛选 |
| 节点 | `GET .../nodes/{key}`、`POST .../submit|confirm|skip|retry|messages` |
| 恢复/审计 | `GET /workflow-instances/{id}/events`、`POST .../external-state-resolution` |

节点 mutation 携带 `expected_revision`，冲突返回 409。外部状态证明接口还要求 `Idempotency-Key`，只有 namespace 管理角色可确认是否允许安全重试。

模板执行配置保存时按 CAS 创建不可变修订：项目模式为 required 时必须选项目，每个非人工节点必须选择兼容 Runtime，Agent 节点还需选择该 Runtime 上已激活的精确 Release。用户创建实例只提交模板版本、名称和业务输入；服务端读取并冻结当前配置修订，不接受实例级 Runtime/Agent 覆盖。
