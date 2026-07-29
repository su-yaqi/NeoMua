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
- `runtime_management` 的控制面仍位于 FastAPI，并明确区分三种管理来源：每个 namespace 自动拥有平台内置 Claude Agent SDK Runtime；service Node 由管理员在目标 Linux/systemd 主机执行 bootstrap 命令后安装受管 SDK；client Node 只安装 NeoMua 管理端并发现本机 Claude Code、Codex。每个 Instance 固定管理来源、引擎类型、系统配置来源、能力报告和模型绑定。
- `agent_management` 拥有引擎中立 Agent 草稿、稳定模型偏好、Skill 文件草稿与不可变发布版本、Tool/MCP/Plugin、canonical Resolver、Release/Runtime Activation 和 Tool Approval。Harness 仅保留只读历史语义；Agent 不再选择 Harness 或供应商路由。
- `project_management` 拥有项目、成员、多仓库、Spec 位置和不可变标准版本绑定；仓库可用性必须来自目标 Runtime 的显式工作区证明。
- `conversation_management` 固定会话创建时的项目与 Runtime Instance；模型或 Agent 参与者/组织 Agent 通过追加式配置修订调整，并以 exact binding 或唯一可解析的 Agent 偏好冻结到后续消息。项目上下文按 commit、Spec 版本和摘要追加快照，历史消息与其配置修订不被改写；消息和委派事件通过数据库游标及可恢复 SSE 交付。
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
- 本地 Compose 以项目范围和显式 `DEV_SLOT` 形成稳定项目名，并映射到 NeoMua 预留端口块；启动前验证项目工作目录归属和宿主机端口占用，冲突即阻断，不随机改端口、不操作其他项目容器。各实例的镜像、网络、Traefik 发现标签和数据卷随 Compose project 隔离。
- Backend 与 Playwright 测试使用当前 slot 派生的独立 test project、无宿主机发布端口的一次性数据库卷，并在验证后清理；测试不复用联调数据库，也不与联调 Runtime Worker 竞争协调任务。
- 能力不可变与目标显式性：Skill/Plugin Version、MCP Revision、Agent Release 内容本身均不可变；Plugin、MCP 与 Agent Release 继续精确锁定自身版本，但 Agent/Plugin/Release 对 Skill 只锁定身份。Skill 当前版本由控制面指针显式决定，Runtime 按目标独立同步，不按 SemVer 猜测，也不做模型、Harness、Tool 或权限降级。
- v0.9 执行绑定以 Runtime Instance 为边界：Agent Release 只保存稳定模型偏好；Conversation、Workflow 和 Task 保存 exact binding 或严格的偏好解析结果。Runtime 配置摘要、能力指纹、模型目录指纹和 effective spec digest 进入不可变执行快照。
- v0.9 的 Runtime 模型证明来自本地持久化 applied 配置、实际 Adapter、能力缓存和已验证模型路由，控制面精确核对后才允许首次模型调用。配置环境按运维与 Runtime 双重 allowlist 清洗；目录、权限、Tool/MCP、能力和超时在执行边界复核，当前无法可靠执行的隔离、网络、CPU、内存或并发策略明确阻断。Node 离线、能力过期或依赖变化会统一使旧 Binding 失效。
- v0.10 平台 Runtime 生命周期和配置由系统拥有，用户不能创建或修改；已启用的 LLM 配置通过具体模型最小调用和 Runtime 能力指纹自动形成 Binding。service/client bootstrap 绑定模式、设备 Ed25519 公钥和签名发行清单，安装状态成对原子落盘；响应丢失只允许同一设备密钥证明恢复，不以其他 Runtime 或凭证降级。
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

本地开发默认只发布 Frontend、Backend 和 Model Gateway 入口；Adminer、Mailcatcher 与本地 Traefik 通过显式 profile 按需启动，PostgreSQL 不发布宿主机端口。该本地 override 不改变仅加载 `compose.yml` 的 staging/production 拓扑。

运行时拓扑：

```text
browser -> backend(control plane) -> PostgreSQL
                         |-> runtime-worker -> platform Claude Agent SDK
service Node (managed SDK) / client Node (signed Claude Code/Codex Adapter)
                         |-> model-gateway -> Anthropic / OpenAI-compatible API
node daemon -- outbound WSS --> backend
backend <-> local volume or S3-compatible immutable artifact storage
operator CLI -> HTTPS/Bearer API；refresh token -> OS Keychain
```

Skill 数据面独立于任务数据面：控制面保存 current/desired/applied 与同步 attempt；平台 Worker 或节点守护进程将签名 Bundle 放入按内容摘要寻址的本地缓存，再原子更新 Runtime 指针。每个任务创建独立的只读绑定并上报 `agent_task_skill_usage`，因此同一 Session 的后续任务可使用新同步版本，而已开始任务不被热替换。

FastAPI 多 worker 不共享内存连接表：节点每次连接写入 PostgreSQL `connection_id`。每次查询和发送前都重验代次；任务和发布先持久化短 reservation，再由当前 generation 下发。独立 maintenance loop 使用 PostgreSQL advisory lock 处理过期 reservation、租约、轮换宽限和发布。

Node 发行和 client Adapter 是两层独立不可变签名对象。service 发行固定 Node Manager、Claude Agent SDK 与 systemd 单元；client 发行只固定 Node Manager、systemd 单元和签名 Registry。client 的绝对执行路径只保存在节点权限受限的本地状态中，控制面仅保存稳定 installation UUID、不可逆文件指纹、Adapter release 和脱敏探测摘要。

## 非功能性约束
| 类型 | 要求 |
|------|------|
| 安全 | 浏览器 HttpOnly Cookie + CSRF、非浏览器 Bearer；密码使用 Argon2/Bcrypt；重置密码接口避免邮箱枚举 |
| 可维护性 | 前后端均基于模板标准目录；接口类型由 OpenAPI 生成；文档需同步到 `context/` |
| 部署 | 所有核心服务均以容器方式运行，依赖 `.env` 注入配置 |
| 测试 | 后端路由与 CRUD 有 Pytest，前端关键页面有 Playwright 覆盖 |
