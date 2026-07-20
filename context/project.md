# 项目概述

## 项目名称
NeoMua

## 背景
项目基于 Full Stack FastAPI Template 演进，保留模板自带的认证、用户、条目管理能力，并新增“空间（namespace）”这一多租户维度，逐步向平台化后台演进。

## 目标

- 提供一个前后端分离、可容器化部署的管理后台基础盘。
- 支持账号认证、个人设置、平台用户管理和基础业务条目管理。
- 支持按空间组织用户，并为后续多租户业务扩展预留统一入口。
- 支持按空间维护、验证并发布 Skill 等 Agent 能力；Agent Release 固定能力身份、模型偏好和引擎中立执行策略，Runtime Instance 异步同步 Skill 当前发布版本并声明经过验证的模型绑定，任务只绑定本地已应用的不可变内容。
- 支持项目上下文、可修订模型/Agent 会话，以及由代码发布、由模板配置执行资源的可恢复 Workflow 业务任务。

## 技术栈
| 层次 | 技术选型 |
|------|--------|
| 后端 | Python 3.10+, FastAPI, SQLModel, Pydantic v2 |
| 前端 | React 19, TypeScript, Vite, TanStack Router, TanStack Query |
| 数据库 | PostgreSQL 18 |
| UI | Tailwind CSS 4, shadcn/ui, Radix UI |
| 测试 | Pytest, Playwright |
| 部署 | Docker Compose, Traefik, Adminer |

## 核心模块
| 模块 | 描述 |
|------|------|
| auth | HttpOnly Cookie/Bearer 双通道、refresh rotation、CSRF、注册与密码恢复 |
| users | 个人资料维护、密码修改、自助注销、平台级用户管理 |
| items | 用户个人条目的增删改查 |
| namespaces | 空间列表、空间 CRUD、空间成员关系与空间上下文选择 |
| llm_configs | 按空间维护多供应商大模型接入配置、连接校验与模型清单同步 |
| runtime_management | 平台内置、服务节点、客户端三类 Runtime；正式 bootstrap/receipt、签名 Adapter 发现、精确模型能力绑定、持久协调任务、Skill 后台同步与任务审计、完整事件审计和签名内容分发 |
| agent_management | 引擎中立 Agent 草稿、模型偏好、可编辑 Skill 工作台、Tool/MCP/Plugin、统一解析、Release/Runtime 激活、审批与 Operator CLI |
| project_management | 项目成员、多 Git 仓库、Spec 位置与不可变标准版本绑定 |
| conversation_management | 固定模型 Chat、单/多 Agent 圆桌、可恢复事件流、项目上下文快照与委派审计 |
| workflow_management | 不可变 Workflow Package/前端注册、有限 DAG、节点修订、附件、确认与恢复 |

## 当前实现状态

- 后端已经实现 `users`、`items`、`namespaces`、`platform`、`llm` 五组主要 API。
- 前端已实现登录/注册/找回密码/重置密码、首页、条目页、用户管理页、个人设置页、空间管理页、大模型接入配置页。
- 前端已接入空间选择器和自定义 `tenantApi`，会自动携带 `X-Namespace-Id`。
- 空间成员管理已形成可用页面流；大模型接入配置支持供应商目录、配置新增/编辑、连接校验、模型同步与手工模型维护。
- v0.4 已实现独立 `runtime-worker`、`model-gateway` 与可安装 `node_runtime`，支持真实 Agent 执行、模型转发、节点 WSS 心跳和离线恢复。
- PostgreSQL 保存用户消息、Agent 消息、工具调用/结果、状态、错误和最终结果；节点使用 SQLite spool 保证断线重放。
- Agent/Skill/MCP/CLI/工作区内容以不可变 ZIP、双签名清单和短期下载令牌分发，节点独立校验并原子应用。
- v0.5 已实现声明式 Skill 与 Plugin、不可变 MCP Revision、目标凭证/校验/Tool 快照、canonical ResolvedAgentSpec、签名 Agent Release、逐目标 Activation/Retry/Rollback 和 frozen Task snapshot。
- `neomua` Operator CLI 使用轮换 refresh session 与系统 Keychain，复用正式 API，支持 dry-run、幂等 mutation、结构化退出码和节点本地 MCP secret 管理。
- v0.6 已实现 AI 工作台、项目配置、Spec 标准、可恢复会话事件流、持久 Runtime Job、固定版本 Workflow 应用与项目任务状态机；不确定的 Runtime、Git、前端组件或外部副作用会阻断并暴露诊断。
- v0.7 将 AI 工作台收敛为 Chat/Agent 双栏会话，项目和 Runtime 创建后固定，模型或 Agent 参与配置通过不可变修订在后续消息生效；Workflow 移至独立用户菜单，并形成“应用目录—实例列表—应用专属创建/详情”的统一外壳。
- Workflow 节点定义和执行继续固化在代码包中，项目及每个可执行节点的 Runtime/Agent 改由模板执行配置管理并生成不可变修订；流程实例只填写任务名称和业务目标。
- Agent、Skill、MCP、Plugin、项目和 Spec 标准的新建流程已收敛为一次完成必要初始配置；用户界面以“唯一标识”替代 Slug。大模型配置可在保存前直接校验连接并同步模型预览。
- v0.7 最终门禁为 Backend 231、Runtime Worker 14、Node Runtime 26、其余 Python 23 和 Playwright 74 项通过；Ruff、Biome、TypeScript/Vite、Alembic 差异检查及完整测试环境健康检查通过。
- v0.8 将 Skill 升级为空间级内容资产：列表进入独立工作台，左侧文件目录树、右侧 Markdown 编辑/预览；草稿经校验发布为不可变签名版本，并由 `current_version_id` 明确当前期望版本。
- Agent、Plugin 和 Agent Release 只绑定 Skill 身份，不锁定 Skill Version。平台 Worker 与节点守护进程在任务链路之外按通知和周期轮询同步 desired/applied 状态；任务执行只绑定本地已应用版本并记录使用证据，不下载、不解包、不重复解析。
- v0.8 集中验证已通过：Alembic 升级至 `e8a1c4b7d902`，Backend 231、Runtime Worker 与 Node Runtime 40、Playwright 74 项通过，前端生产构建、Ruff 与差异格式检查通过。
- v0.9 将机器与执行引擎拆分为 Runtime Node 和一对多 Runtime Instance；Claude Code、Codex 等 Harness 收敛为 Runtime 内部引擎适配器，不再提供 Agent 侧 Harness 配置写入。
- Runtime Instance 通过修订、能力报告、模型目录与已验证绑定声明可执行模型；Agent Release 只声明稳定模型偏好和引擎中立策略。Conversation、Agent 组与 Workflow 在不可变配置中选择具体模型或严格解析 Agent 偏好，任务启动前冻结 Runtime、引擎、模型、路由和能力证据，禁止隐式默认、模糊匹配或静默回退。
- v0.9 需求一致性与安全整改复核已通过：Runtime 模型证据来自本地 applied 配置和已验证路由；安全策略在执行边界应用，无法可靠执行的策略明确阻断；离线/过期依赖、迁移歧义、冻结行为、逐次用量和并发语义均已关闭。
- v0.9 集中验证为 Backend 213、Runtime Worker 19、Node Runtime 27、Playwright 74 项通过；前端生产构建、Ruff、变更范围 Biome、Alembic upgrade/check 与差异格式检查通过，数据库 head 为 `fc5a7b9d1e34`；已合并到本地 `master`。
- v0.10 将 Runtime 收敛为平台内置、服务节点和客户端三种系统来源。平台通过持久协调和逐模型真实调用形成 Binding；service/client 通过签名发行、设备 receipt 和发现 generation 自动注册，客户端任务固定本地 installation UUID、路径指纹、Adapter 与登录证据，不接受业务侧命令配置或静默回退。
- v0.10 集中验证为 Backend 218、Runtime Worker 与 Node Runtime 54、Runtime Playwright 3 项通过；Alembic 完整升级、v0.10 回退和再升级、前端生产构建、Ruff/Biome 及生成客户端检查通过，数据库 head 为 `0a10b2c3d4e5`。

## 版本状态
| 版本 | 状态 | 说明 |
|------|------|------|
| v0.1 | 已完成 | 模板基础能力 + 命名空间模型、空间管理 API 与空间选择器接入 |
| v0.2 | 已完成 | 空间成员管理页、平台用户空间分配收敛与测试链路修正 |
| v0.3 | 已完成 | 空间级多供应商大模型接入配置、连接校验与模型同步能力 |
| v0.4 | 已完成 | Agent Runtime、节点守护进程、可靠任务执行与签名内容分发 |
| v0.5 | 已完成 | 受管 Agent 能力、统一运行时装配、Release/Activation、Tool 审批与 Operator CLI |
| v0.6 | 已完成 | 项目上下文、Chat/多 Agent 工作台、代码化 Workflow 应用与协作任务执行 |
| v0.7 | 已完成 | Chat/Agent 工作台、完整创建体验、独立 Workflow 应用与模板执行配置 |
| v0.8 | 已完成 | Skill 文件工作台、不可变发布版本、身份绑定、Runtime 异步同步与任务使用审计 |
| v0.9 | 已完成 | Runtime Instance、可信执行证据与安全边界已通过复核并合入 `master` |
| v0.10 | 待评审 | 三类系统 Runtime、正式节点安装、签名客户端发现和自动模型路由已实现并完成集中验证 |
