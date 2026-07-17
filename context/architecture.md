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
runtime_management -> namespaces
runtime_management -> llm_configs
agent_management -> namespaces
agent_management -> llm_configs
agent_management -> runtime_management
project_management -> namespaces / runtime_management
conversation_management -> project_management / agent_management / runtime_management
workflow_management -> project_management / conversation_management / agent_management / runtime_management

frontend routes --> frontend components --> client SDK / tenantApi
backend routes --> deps / crud --> models --> db
```

### 边界说明

- `auth` 负责 Cookie/Bearer 双通道认证，拥有可轮换、可吊销的 `refresh_session`；用户账号仍归 `users`。
- `users` 负责账号生命周期与平台级用户管理，是 `items` 与 `namespaces` 的上游模块。
- `items` 只管理归属到 `owner_id` 的个人条目，不感知空间维度。
- `namespaces` 负责空间实体、用户-空间关联关系和空间管理员权限校验。
- `llm_configs` 负责空间级供应商接入配置；`runtime_management` 只通过同空间、已启用的配置引用模型。
- `runtime_management` 的控制面仍位于 FastAPI；`runtime-worker` 独占平台 Claude SDK/CLI 生命周期并领取持久化 `runtime_job`，同时在任务循环之外轮询并同步 Skill；`model-gateway` 提供 Anthropic Messages API 并做供应商转换。
- `agent_management` 拥有 Agent/Harness 草稿、Skill 文件草稿与不可变发布版本、Tool/MCP/Plugin、canonical Resolver/Claude Adapter、Release/Activation 和 Tool Approval。Agent、Plugin 与 Release 只声明 Skill 身份；运行时不读取草稿，按 Skill 的 `current_version_id` 同步签名 Bundle。
- `project_management` 拥有项目、成员、多仓库、Spec 位置和不可变标准版本绑定；仓库可用性必须来自目标 Runtime 的显式工作区证明。
- `conversation_management` 固定会话创建时的项目与 Runtime；模型或 Agent 参与者/组织 Agent 通过追加式配置修订调整，仅影响后续消息。项目上下文按 commit、Spec 版本和摘要追加快照，历史消息与其配置修订不被改写；消息和委派事件通过数据库游标及可恢复 SSE 交付。
- `workflow_management` 从仓库内 `workflow_apps/<slug>` 校验并注册不可变 Package；节点定义与执行逻辑属于代码包，项目及节点 Runtime/Agent 属于模板执行配置。数据库持久化配置修订、有限 DAG 实例、节点修订、执行轮次、Gate、确认、附件、产物和事件。
- 节点守护进程只建立出站 WSS，使用短期握手签名、20 秒心跳、60 秒离线阈值和数据库连接代次；离线不删除配对。
- 节点直连仅接受经节点实测通过的 Anthropic Messages API；非兼容供应商必须经 Model Gateway。

## 关键架构决策

- FastAPI + SQLModel：沿用模板生态，降低脚手架维护成本。
- OpenAPI 生成客户端：前端对标准接口使用自动生成 SDK，减少手写类型漂移。
- generated client 与 `tenantApi` 共用带 credentials、CSRF 和单飞 refresh 的 Axios 实例；`tenantApi` 继续负责 namespace Header 与 SSE。
- `X-Namespace-Id` + `require_namespace_admin`：通过请求头或查询参数绑定当前空间上下文。
- 供应商预置目录内置在后端服务层：以统一 `ProviderDefinition` 描述不同供应商的接入参数、鉴权方式、探活和模型发现能力。
- 密钥仅以版本化 AES-GCM v2 密文落库、对外固定显示 `****`；reader 在迁移期兼容 v1，自带独立 purpose/version AAD。
- 单体服务 + Compose 编排：当前规模下优先简化开发、测试和部署链路。
- 能力不可变与目标显式性：Skill/Plugin Version、MCP Revision、Agent Release 内容本身均不可变；Plugin、MCP 与 Agent Release 继续精确锁定自身版本，但 Agent/Plugin/Release 对 Skill 只锁定身份。Skill 当前版本由控制面指针显式决定，Runtime 按目标独立同步，不按 SemVer 猜测，也不做模型、Harness、Tool 或权限降级。
- Skill 发布、同步和使用解耦：发布阶段完成文件安全检查、Manifest 解析、完整校验、摘要和签名；同步阶段由通知与周期轮询驱动，按摘要下载、验签、缓存并原子切换 desired/applied；任务阶段只绑定 Runtime 本地已应用的不可变目录并写入版本证据，不访问控制面、不下载、不解包、不再次解析。
- Skill 同步采用两阶段提交和保守失败语义：验证成功后再提交 applied 指针；同步失败保留上一已应用版本。首次尚无可用版本时阻断依赖该 Skill 的激活或任务，已有版本时继续使用旧 applied 并明确展示差异，绝不静默移除能力。
- MCP secret 分域：平台 target 使用 AES-GCM 密文；节点 target 只保存 `secret_ref`，本地 Keychain 指纹通过心跳上报，变化或移除使 target `stale`。
- Workflow 新实例必须引用模板当前完整的不可变执行配置修订；每个可执行节点使用该修订明确指定的 Runtime，Agent 节点同时固定精确 Agent Release。缺失配置、能力、仓库证明、Validator 或外部状态时停止，不从实例参数或项目默认值猜测目标，也不自动重试。
- 仓库探测、Workflow Validator 和 Handler 统一使用 `runtime_job`。平台 Worker 或节点按租约领取并回写结果，FastAPI 只做入队、对账和状态推进，不以进程内后台任务替代持久执行。
- Workflow 前端只按任务固定的 `workflow_application.component_key` 从编译时注册表加载。未知 key 明确阻断；数据库不保存模块 URL，也不加载远程 JavaScript 或通用降级页面。
- 用户侧 Workflow 采用统一应用外壳：`/workflows` 是卡片目录，`/apps/:workflowSlug` 提供应用切换和实例列表；新建页与实例详情由固定版本组件独立实现，不以通用表单替代不可用组件。
- 有副作用的节点必须返回完成证明。状态不确定时进入 `needs_manual_resolution`，仅 namespace Admin/Developer 提交审计证据且证明未执行或已补偿后才允许重试。

## 部署拓扑

```text
browser
  -> frontend (Vite build / Nginx)
  -> backend (FastAPI)
  -> db (PostgreSQL)
```

Adminer 仅位于显式 `debug` Compose profile，不属于默认生产拓扑。
Traefik 负责域名路由与 HTTPS 终止。
prestart 容器负责迁移前准备与初始化检查。

运行时拓扑：

```text
browser -> backend(control plane) -> PostgreSQL
                         |-> runtime-worker -> Claude Agent SDK/CLI
Claude SDK / node ------>|-> model-gateway -> Anthropic / OpenAI-compatible API
node daemon -- outbound WSS --> backend
backend <-> local volume or S3-compatible immutable artifact storage
operator CLI -> HTTPS/Bearer API；refresh token -> OS Keychain
```

Skill 数据面独立于任务数据面：控制面保存 current/desired/applied 与同步 attempt；平台 Worker 或节点守护进程将签名 Bundle 放入按内容摘要寻址的本地缓存，再原子更新 Runtime 指针。每个任务创建独立的只读绑定并上报 `agent_task_skill_usage`，因此同一 Session 的后续任务可使用新同步版本，而已开始任务不被热替换。

FastAPI 多 worker 不共享内存连接表：节点每次连接写入 PostgreSQL `connection_id`。每次查询和发送前都重验代次；任务和发布先持久化短 reservation，再由当前 generation 下发。独立 maintenance loop 使用 PostgreSQL advisory lock 处理过期 reservation、租约、轮换宽限和发布。

## 非功能性约束
| 类型 | 要求 |
|------|------|
| 安全 | 浏览器 HttpOnly Cookie + CSRF、非浏览器 Bearer；密码使用 Argon2/Bcrypt；重置密码接口避免邮箱枚举 |
| 可维护性 | 前后端均基于模板标准目录；接口类型由 OpenAPI 生成；文档需同步到 `context/` |
| 部署 | 所有核心服务均以容器方式运行，依赖 `.env` 注入配置 |
| 测试 | 后端路由与 CRUD 有 Pytest，前端关键页面有 Playwright 覆盖 |
