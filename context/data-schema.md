# 数据模型

> 当前数据库实体按模块定义在 `backend/app/models.py` 与 `backend/app/**/models.py`，并通过 Alembic 管理迁移。

## 命名规范

- 表名：单数、小写下划线或模板既有命名（当前为 `user`、`item`、`namespace`、`user_namespace_link`）
- 字段名：小写下划线
- 主键：统一使用 UUID
- 外键：`<entity>_id` 形式

## 公共字段约定

当前业务表并未统一具备软删除字段，已实现的公共字段如下：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| created_at | timestamptz | 创建时间；`User`、`Item`、`Namespace`、`LlmProviderConfig`、`LlmProviderModel` 已实现 |
| updated_at | timestamptz | 更新时间；当前 `LlmProviderConfig`、`LlmProviderModel` 已实现 |

## 实体关系概览

```text
User 1 ---- N Item
User 1 ---- N RefreshSession
User 1 ---- N UserNamespaceLink N ---- 1 Namespace
Namespace 1 ---- N LlmProviderConfig 1 ---- N LlmProviderModel
Namespace 1 ---- N RuntimeNode / RuntimeInstance / AgentTask / RuntimeArtifact
RuntimeNode 1 ---- N RuntimeInstance / NodeCredential / AgentTask / ArtifactDeployment
AgentTask 1 ---- N AgentEvent
RuntimeInstance 1 ---- N RuntimeConfigurationRevision / RuntimeCapabilityReport / RuntimeModelBinding / RuntimeJob
Namespace 1 ---- N AgentDefinition / SkillDefinition / McpServer / Plugin
AgentDefinition 1 ---- 1 AgentDraft 1 ---- N capability bindings
SkillDefinition 1 ---- 1 SkillDraft 1 ---- N SkillDraftFile
SkillDefinition 1 ---- N SkillVersion / SkillCurrentVersionChange
AgentRelease 1 ---- N AgentActivation 1 ---- N AgentDeployment
RuntimeInstance N ---- N AgentRelease (via RuntimeAgentRelease)
RuntimeInstance N ---- N SkillDefinition (via RuntimeSkillState)
RuntimeSkillState 1 ---- N RuntimeSkillSyncAttempt
AgentTask 1 ---- N AgentTaskSkillUsage
AgentTask 1 ---- 1 AgentTaskModelUsage
```

## 表结构

### runtime_management 表组

| 表 | 关键职责 |
|---|---|
| `runtime_node` | 机器身份、公钥、指纹、service/client 管理模式、Adapter Registry 摘要、心跳、发现与吊销时间 |
| `runtime_instance` | namespace 内执行引擎实例；记录 `platform_builtin/service_managed/client_discovered` 管理来源、生命周期 source key、配置和能力状态 |
| `runtime_configuration_revision` | Runtime 不可变系统配置；增加 `system_builtin/service_manifest/client_adapter` 来源和 Adapter 执行引用 |
| `runtime_capability_report` | Runtime/engine/Adapter 版本、能力、发现模型、generation、指纹与上报时间 |
| `llm_model_definition` | namespace 内稳定模型身份；以 provider family 与精确 model key 表达偏好 |
| `runtime_model_binding` / `runtime_model_validation_attempt` | 稳定模型在具体 Runtime 上的精确路由、engine model id、验证状态与证据 |
| `runtime_secret` | 运行时密钥加密载荷；浏览器仅见掩码 |
| `agent_session` | 平台多轮会话与 Claude SDK session ID |
| `agent_task` | 不可变执行快照、目标节点、任务类型、状态、租约、幂等键和 retry 链 |
| `agent_event` | 按 `(task_id, sequence)` 唯一保存用户、Agent、工具、状态、错误和结果事件 |
| `runtime_job` | 仓库探测、Workflow Validator/Handler 的持久任务；保存目标 Runtime、租约、修订、幂等键、结果与人工处理状态 |
| `node_enrollment_token` | 一次性 bootstrap HMAC；保存绑定管理模式、预检时间、设备公钥指纹和签名发行清单摘要，不保存明文 |
| `node_distribution_release` | 不可变 Node 发行清单、ZIP 摘要/大小、逐文件摘要、模式/系统/架构、签名和生效通道 |
| `runtime_adapter_release` | 不可变 Adapter 身份、引擎、版本范围、有限发现合同、执行合同、摘要和签名 |
| `node_bootstrap_session` / `node_bootstrap_attempt` | 从 waiting、preflight、staged、enrolled/activated 到 reconciled 的正式安装状态与追加诊断；保存同设备幂等恢复密文，不保存明文凭证 |
| `node_installation_receipt` | 设备签名的精确发行/组件/逻辑安装引用；只有与平台签名清单一致时才能成为 Node current receipt |
| `runtime_discovery_observation` | 设备签名完整 generation 中每个稳定安装或候选的 available/missing/blocked 摘要；不保存本地路径或凭证 |
| `runtime_control_decision` | client bootstrap 自动授权以及 Admin pause/resume 的操作者和证据历史 |
| `runtime_installation_migration_receipt` | 预留给正式 adopt 流程的旧 Runtime 与本地 installation UUID 设备签名关联；当前无 adopt 时不猜测合并 |
| `platform_runtime_reconcile_job` / `platform_runtime_reconcile_attempt` | 按协调输入指纹幂等的持久任务、attempt、结果和脱敏错误 |
| `llm_provider_model_validation` | Provider Config 指纹下逐模型最小真实调用证据、有效期和错误；连接探活不能替代 |
| `node_credential` | 90 天设备凭证、密钥指纹、轮换链与吊销时间；不保存私钥 |
| `runtime_artifact` | namespace 内不可变内容哈希、逻辑目标、清单、签名和存储键 |
| `artifact_release` | 有效期内的发布或回滚意图 |
| `artifact_deployment` | 每节点每次发布尝试及 pending/dispatched/applied/failed/expired 状态 |
| `runtime_node_artifact` | 节点每个逻辑目标的 current/previous 制品指针 |
| `runtime_skill_state` | 每个 Runtime/Skill 的订阅数、desired/applied 版本与摘要、代次、同步状态和重试信息 |
| `runtime_skill_sync_attempt` | 每次 Skill 后台同步的目标代次、版本、摘要、触发来源、下载量、状态和错误 |
| `agent_task_skill_usage` | 单次任务实际绑定的 Skill 身份、版本、摘要和 Runtime applied generation 证据 |
| `agent_task_model_usage` | 单次任务冻结并由 Runtime 回报的 Runtime、engine、Adapter、模型 Binding、路由和摘要证据 |

`agent_task` 额外以 `dispatch_connection_id/dispatch_reserved_until` 记录短期发送预留；预留不改变 QUEUED，超时后可重新领取。`artifact_deployment` 固化 `logical_target`，部分唯一索引保证每个 `(node_id, logical_target)` 最多一个 pending/dispatched。`node_credential` 记录 replacement 签发时间和宽限截止时间。任务状态只允许显式迁移；租约过期变为 interrupted，不自动 retry。

### agent_management 表组（v0.5-v0.9）

| 表组 | 关键职责 |
|---|---|
| `agent_definition` / `agent_draft` | 空间级 Agent 身份、CAS 草稿、稳定模型偏好和引擎中立执行策略 |
| `harness_profile` | v0.9 仅保留只读历史语义，不参与新 Agent 写入或任务路由 |
| `skill_definition` / `skill_version` | Skill 身份、显式 `current_version_id`、不可变 Bundle 摘要、Manifest、签名与校验结果 |
| `skill_draft` / `skill_draft_file` | 单一可编辑草稿、CAS revision、校验证据，以及按规范化路径存储的文本/二进制文件 |
| `skill_current_version_change` | current version 发布、切换或回滚的幂等审计记录 |
| `tool_definition` / `namespace_tool_policy` / `agent_draft_tool_policy` | 内置 Tool 基线、空间只可收紧策略和 Agent 意图 |
| `mcp_server` / `mcp_server_revision` / `mcp_target_binding` | MCP 身份、不可变 transport/config Revision 和明确运行时目标 |
| `mcp_platform_secret` / `mcp_validation_attempt` / `mcp_tool_snapshot` | 平台密文、目标校验历史和限定名 Tool Schema 快照 |
| `mcp_runtime_instance` / `mcp_runtime_event` | 按 runtime/revision/指纹复用的实例与脱敏生命周期事件 |
| `plugin` / `plugin_draft` / `plugin_version` | 声明式能力包草稿、精确 dependency lock 和签名不可变版本 |
| `agent_draft_skill/plugin/mcp` | Skill 按身份和 enabled 绑定；Plugin/MCP 仍绑定精确版本或 Revision；每次变更推进 revision 并使验证失效 |
| `agent_release` / `agent_release_component` | canonical ResolvedAgentSpec、依赖锁、manifest、签名和来源链 |
| `agent_activation` / `agent_deployment` | 激活批次及每个 Runtime target 的独立 attempt/status/error |
| `runtime_agent_release` | 每个 `(runtime, agent)` 的 current/previous Release 指针和 digest |
| `agent_activation_precheck` / `agent_release_runtime_compatibility` | Runtime 目标预检证据、能力/模型目录摘要与可重建兼容诊断 |
| `tool_approval_request` | 绑定 task revision、tool call 与 args digest 的可过期审批 |
| `cli_session` | Operator CLI 轮换 refresh token HMAC、family、绝对到期与吊销链 |

`agent_session` 与 `agent_task` 增加 Agent Release、Runtime binding 和 `resolved_spec_digest` 引用；Task snapshot 冻结 Release 与策略但不内嵌 Skill Version 或平台/节点 secret value。`agent_draft_skill.skill_version_id` 仅为迁移兼容保留且可空，新写入使用 `skill_id + enabled`；Release 的 `resolved_spec.skills` 保存 Skill 身份元数据，不保存版本号。

Skill 草稿每个 Skill 只允许一条；文件路径在草稿内唯一并拒绝绝对路径、`..`、符号链接和越界内容。发布要求当前 revision 已通过同摘要校验，生成不可变 `skill_version` 后原子更新 `current_version_id` 并记录 change。Runtime desired 来自当前版本，applied 仅在对应 generation 的缓存验证和提交成功后推进；旧 applied 在失败时保留。

### refresh_session

| 字段 | 说明 |
|---|---|
| `id` | refresh session 主键 |
| `family_id` | 轮换链；检测重放时整链吊销 |
| `user_id` | 所属用户，用户删除时级联删除 |
| `token_hash` | refresh token 的 keyed HMAC，唯一；不保存明文 |
| `expires_at` | 绝对过期时间 |
| `replaced_by_id` | 下一枚 refresh session |
| `revoked_at` | 轮换、退出、重放或用户失效时写入 |
| `created_at/last_used_at` | 审计时间 |

删除语义：用户删除级联 refresh session；runtime/节点/任务/事件按现有外键级联或 SET NULL；制品被 deployment 引用时 RESTRICT，不能删除破坏历史审计。

### user
系统用户表，保存登录账号、平台权限和基础资料。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| email | varchar(255) | 否 | - | 唯一邮箱，登录标识 |
| is_active | boolean | 否 | true | 是否启用 |
| is_superuser | boolean | 否 | false | 是否平台超级管理员 |
| full_name | varchar(255) | 是 | null | 用户姓名 |
| hashed_password | varchar | 否 | - | 加密后的密码 |
| created_at | timestamptz | 是 | now() | 创建时间 |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| ix_user_email | email | 唯一 | 登录与注册去重 |

### item
个人条目表，归属于单个用户。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| title | varchar(255) | 否 | - | 条目标题 |
| description | varchar(255) | 是 | null | 条目描述 |
| created_at | timestamptz | 是 | now() | 创建时间 |
| owner_id | uuid | 否 | - | 归属用户 |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| fk_item_owner_id | owner_id | 普通 | 用户条目查询与级联删除 |

### namespace
空间表，表示租户/工作空间层级的组织单元。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| name | varchar(255) | 否 | - | 空间名称 |
| code | varchar(64) | 否 | - | 空间编码，唯一 |
| is_active | boolean | 否 | true | 是否启用 |
| created_at | timestamptz | 是 | now() | 创建时间 |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| ix_namespace_name | name | 唯一 | 名称去重 |
| ix_namespace_code | code | 唯一 | 编码去重 |

### user_namespace_link
用户与空间的关联表，同时保存空间内角色。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| user_id | uuid | 否 | - | 关联用户 |
| namespace_id | uuid | 否 | - | 关联空间 |
| role | enum | 否 | user | 空间角色：`admin` / `developer` / `user` |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| uq_user_namespace_link_user_namespace | user_id, namespace_id | 唯一 | 防止同一用户重复加入同一空间 |

### llm_provider_config
空间级大模型供应商接入配置表。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| namespace_id | uuid | 否 | - | 所属空间 |
| config_name | varchar(255) | 否 | - | 配置名称；同一空间内唯一 |
| provider_slug | varchar(128) | 否 | - | 供应商标识 |
| provider_display_name | varchar(255) | 否 | - | 供应商展示名快照 |
| auth_type | enum | 否 | - | 鉴权方式，如 `api_key` / `oauth_external` / `aws_sdk` |
| base_url | varchar(1024) | 否 | - | 接入地址 |
| secret_ciphertext | text | 是 | null | 加密后的密钥载荷 |
| secret_masked | varchar(255) | 是 | null | 对外展示的密钥掩码 |
| extra_config | json | 否 | `{}` | 供应商扩展参数 |
| supports_health_check | boolean | 否 | false | 是否支持在线校验 |
| supports_model_discovery | boolean | 否 | false | 是否支持自动拉取模型 |
| validation_status | enum | 否 | `unverified` | 最近一次校验状态 |
| validation_message | varchar(1024) | 是 | null | 最近一次校验/同步提示 |
| last_validated_at | timestamptz | 是 | null | 最近校验时间 |
| enabled | boolean | 否 | true | 配置是否启用 |
| created_by | uuid | 否 | - | 创建人 |
| updated_by | uuid | 否 | - | 最近更新人 |
| created_at | timestamptz | 是 | now() | 创建时间 |
| updated_at | timestamptz | 是 | now() | 更新时间 |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| uq_llm_provider_config_namespace_name | namespace_id, config_name | 唯一 | 同一空间内配置名称去重 |

### llm_provider_model
接入配置下的模型清单表，既存储自动发现模型，也存储手工补录模型。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| provider_config_id | uuid | 否 | - | 所属接入配置 |
| model_id | varchar(255) | 否 | - | 模型标识 |
| display_name | varchar(255) | 是 | null | 展示名 |
| source_type | enum | 否 | - | 来源：`discovered` / `manual` |
| is_enabled | boolean | 否 | false | 当前配置下该模型是否可用 |
| sync_status | enum | 否 | `active` | 同步状态：`active` / `stale` / `sync_failed` |
| raw_metadata | json | 否 | `{}` | 供应商原始模型元数据快照 |
| last_synced_at | timestamptz | 是 | null | 最近同步时间 |
| created_at | timestamptz | 是 | now() | 创建时间 |
| updated_at | timestamptz | 是 | now() | 更新时间 |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| uq_llm_provider_model_config_model | provider_config_id, model_id | 唯一 | 防止同一配置重复记录相同模型 |

## v0.6 项目、会话与 Workflow

| 聚合 | 核心表 | 关键不可变性/约束 |
|------|--------|------------------|
| 项目 | `project`、`project_member`、`project_repository`、`project_spec_location` | 项目只归档；成员必须来自 namespace；仓库无主次，路径拒绝绝对路径与 `..` 逃逸 |
| Spec 标准 | `spec_standard`、`spec_standard_version`、`project_spec_binding` | 平台/namespace slug 唯一；版本与 content digest 不可变；项目绑定精确版本 |
| 会话 | `conversation`、`conversation_agent`、`conversation_context_snapshot`、`conversation_message`、`conversation_event`、`agent_delegation`、`conversation_attachment` | 创建按 creator + idempotency key 唯一；每轮消息幂等；仅一个主 Agent；上下文快照与 SSE 事件追加式 |
| Workflow 定义 | `workflow_template`、`workflow_template_version`、`workflow_node_definition`、`workflow_edge_definition`、`workflow_application`、`namespace_workflow_enablement` | Package digest 和版本不可变；节点/边固定为有限 DAG；namespace 默认版本必须启用 |
| Workflow 运行 | `workflow_instance`、`workflow_node_instance`、`workflow_node_revision`、`workflow_node_execution`、`workflow_gate_result`、`workflow_confirmation`、`workflow_artifact`、`workflow_attachment`、`workflow_event` | 任务固定模板版本/Package/上下文；修订、附件和事件追加；每输入修订仅一个 active execution；完成任务只读 |

`conversation.current_context_snapshot_id` 与 `workflow_node_instance.current_revision_id` 使用具名 `use_alter` 外键，既保留当前指针，也让空库迁移可确定排序。删除历史模板、版本、Release 或已引用标准均由 `RESTRICT` 阻止。

`conversation_event` 与 `workflow_event` 均以聚合内单调 `sequence` 作为 SSE 恢复游标。`conversation_attachment` 与 `workflow_attachment` 只接受严格扫描后的 UTF-8 TXT/Markdown/JSON，数据库保存不可变摘要和受控存储引用，公开 API 不返回 `storage_ref`。

## v0.7 会话与 Workflow 配置修订

| 聚合 | 新增/调整 | 关键约束 |
|------|-----------|----------|
| 会话配置 | `conversation_configuration_revision`；`conversation.current_configuration_revision_id`；`conversation_message.configuration_revision_id`；`conversation_agent` 增加 active/配置修订生命周期字段 | 项目与 Runtime 在会话创建后不可修改；Chat 修订模型，Agent 修订参与者和唯一组织 Agent；历史消息固定其实际使用的修订 |
| Workflow 上下文 | `workflow_instance.context_mode`、可空 `project_id`、`project_context_snapshot` | 模板 `project_mode` 决定项目必选、可选或禁用；独立实例不伪造项目，项目实例冻结创建时项目配置 |
| Workflow Agent | `workflow_node_definition.agent_role_key`、`workflow_instance_agent_binding` | Agent 节点声明逻辑角色；实例绑定精确 Runtime、Agent Release 与 Resolved Spec digest |
| Workflow 执行配置 | `namespace_workflow_configuration`、`workflow_execution_configuration_revision`、`workflow_execution_node_binding`；`workflow_instance.execution_configuration_revision_id` | 每个 namespace/模板版本只有一个当前配置指针；保存使用 expected revision 并创建不可变新修订；每个非人工节点必须明确 Runtime，Agent 节点还必须明确 Release；实例创建时冻结当前修订 |

迁移链在 v0.7-v0.9 建立会话、Workflow、Skill 与 Runtime Instance 执行证据。v0.10 增加三类 Runtime 管理来源、Node 模式、配置/Binding 来源、bootstrap 设备绑定和平台内置 Runtime 唯一约束；当前目标 Alembic head 为 `0a10b2c3d4e5`。
