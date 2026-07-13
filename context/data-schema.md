# 数据模型

> 当前数据库实体集中定义在 `backend/app/models.py`，并通过 Alembic 管理迁移。

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
Namespace 1 ---- N RuntimeProfile / RuntimeNode / AgentTask / RuntimeArtifact
RuntimeNode 1 ---- N NodeCredential / AgentTask / ArtifactDeployment
AgentTask 1 ---- N AgentEvent
Namespace 1 ---- N AgentDefinition / SkillDefinition / McpServer / Plugin
AgentDefinition 1 ---- 1 AgentDraft 1 ---- N exact capability bindings
AgentRelease 1 ---- N AgentActivation 1 ---- N AgentDeployment
RuntimeProfile N ---- N AgentRelease (via RuntimeAgentRelease)
```

## 表结构

### runtime_management 表组

| 表 | 关键职责 |
|---|---|
| `runtime_profile` | namespace 内平台或节点运行时的模型、路由、工具与目录策略；平台使用部分唯一索引 |
| `runtime_secret` | 运行时密钥加密载荷；浏览器仅见掩码 |
| `agent_session` | 平台多轮会话与 Claude SDK session ID |
| `agent_task` | 不可变执行快照、目标节点、任务类型、状态、租约、幂等键和 retry 链 |
| `agent_event` | 按 `(task_id, sequence)` 唯一保存用户、Agent、工具、状态、错误和结果事件 |
| `runtime_node` | 节点公钥、指纹、版本、心跳、连接代次、配置修订和吊销时间 |
| `node_enrollment_token` | 一次性注册令牌 HMAC；仅保存哈希、有效期和消费时间 |
| `node_credential` | 90 天设备凭证、密钥指纹、轮换链与吊销时间；不保存私钥 |
| `runtime_artifact` | namespace 内不可变内容哈希、逻辑目标、清单、签名和存储键 |
| `artifact_release` | 有效期内的发布或回滚意图 |
| `artifact_deployment` | 每节点每次发布尝试及 pending/dispatched/applied/failed/expired 状态 |
| `runtime_node_artifact` | 节点每个逻辑目标的 current/previous 制品指针 |

`agent_task` 额外以 `dispatch_connection_id/dispatch_reserved_until` 记录短期发送预留；预留不改变 QUEUED，超时后可重新领取。`artifact_deployment` 固化 `logical_target`，部分唯一索引保证每个 `(node_id, logical_target)` 最多一个 pending/dispatched。`node_credential` 记录 replacement 签发时间和宽限截止时间。任务状态只允许显式迁移；租约过期变为 interrupted，不自动 retry。

### agent_management 表组（v0.5）

| 表组 | 关键职责 |
|---|---|
| `agent_definition` / `agent_draft` / `harness_profile` | 空间级 Agent 身份、CAS 草稿和受限 Claude Harness 配置 |
| `skill_definition` / `skill_version` | 声明式 Skill 身份、不可变 ZIP 摘要、manifest 与校验结果 |
| `tool_definition` / `namespace_tool_policy` / `agent_draft_tool_policy` | 内置 Tool 基线、空间只可收紧策略和 Agent 意图 |
| `mcp_server` / `mcp_server_revision` / `mcp_target_binding` | MCP 身份、不可变 transport/config Revision 和明确运行时目标 |
| `mcp_platform_secret` / `mcp_validation_attempt` / `mcp_tool_snapshot` | 平台密文、目标校验历史和限定名 Tool Schema 快照 |
| `mcp_runtime_instance` / `mcp_runtime_event` | 按 runtime/revision/指纹复用的实例与脱敏生命周期事件 |
| `plugin` / `plugin_draft` / `plugin_version` | 声明式能力包草稿、精确 dependency lock 和签名不可变版本 |
| `agent_draft_skill/plugin/mcp` | 草稿到精确能力版本的绑定；每次变更推进 revision 并使验证失效 |
| `agent_release` / `agent_release_component` | canonical ResolvedAgentSpec、依赖锁、manifest、签名和来源链 |
| `agent_activation` / `agent_deployment` | 激活批次及每个 Runtime target 的独立 attempt/status/error |
| `runtime_agent_release` | 每个 `(runtime, agent)` 的 current/previous Release 指针和 digest |
| `tool_approval_request` | 绑定 task revision、tool call 与 args digest 的可过期审批 |
| `cli_session` | Operator CLI 轮换 refresh token HMAC、family、绝对到期与吊销链 |

`agent_session` 与 `agent_task` 增加 Agent Release、Runtime binding 和 `resolved_spec_digest` 引用；Task snapshot 冻结版本与策略但不含平台或节点 secret value。

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
| 会话 | `conversation`、`conversation_agent`、`conversation_context_snapshot`、`conversation_message`、`agent_delegation`、`conversation_attachment` | 创建按 creator + idempotency key 唯一；每轮消息幂等；仅一个主 Agent；上下文快照追加式 |
| Workflow 定义 | `workflow_template`、`workflow_template_version`、`workflow_node_definition`、`workflow_edge_definition`、`workflow_application`、`namespace_workflow_enablement` | Package digest 和版本不可变；节点/边固定为有限 DAG；namespace 默认版本必须启用 |
| Workflow 运行 | `workflow_instance`、`workflow_node_instance`、`workflow_node_revision`、`workflow_node_execution`、`workflow_gate_result`、`workflow_confirmation`、`workflow_artifact`、`workflow_event` | 任务固定模板版本/Package/上下文；修订和事件追加；每输入修订仅一个 active execution；完成任务只读 |

`conversation.current_context_snapshot_id` 与 `workflow_node_instance.current_revision_id` 使用具名 `use_alter` 外键，既保留当前指针，也让空库迁移可确定排序。删除历史模板、版本、Release 或已引用标准均由 `RESTRICT` 阻止。
