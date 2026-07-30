# 设计文档：空间级 Agent 与 Harness 配置管理（v0.5 阶段一）

- 版本：v0.5 阶段一
- 来源 PRD：`context/prds/v0.5/namespace-agent-harness-management.md`
- 创建时间：2026-07-12
- 状态：待实现

## 1. 范围与边界

### 1.1 本阶段交付

建立 NeoMua 第一个正式的 Agent 管理模块，使 Namespace Admin 可创建、编辑、复制、归档 Agent，并通过显式草稿修订管理并发修改；同时管理可复用的 Claude Harness Profile 与结构化 CLI 配置。

### 1.2 与 v0.4 的关系（只新增，不动 v0.4）

本阶段**完全不修改** v0.4 的 `RuntimeProfile`、`agent_tasks` 路由、运行时页面和任务执行链路。v0.4 的直接建任务入口继续工作，直到 v0.5 阶段六（release-activation）才迁移。`AgentDraft` 只通过只读依赖引用 namespace 内已启用的模型和 runtime 上报的 CLI/SDK 版本，不修改这些来源表。

### 1.3 不在本阶段范围

- 发布或激活 Agent（阶段六）。
- Resolver / `ResolvedAgentSpec` / Claude Adapter（阶段五）。
- Skill / Tool / MCP / Plugin 绑定与能力页签（阶段二/三/四）。Agent 编辑器的"能力"和"校验与发布"两个页签在本阶段保留入口但置为"待后续阶段启用"。
- 远程安装/升级 Claude Code CLI 或 SDK 二进制。
- NeoMua Operator CLI（阶段七）。

## 2. 模块结构

新建 `app/agent_management/` 包，第一阶段包含：

```
app/agent_management/
├── __init__.py
├── models.py     # AgentDefinition, AgentDraft, HarnessProfile SQLModel 表
├── catalog.py    # harness 类型目录、环境变量 allowlist/denylist 常量与校验
├── service.py    # CAS 保存、草稿校验、Profile 安全校验业务逻辑
├── schemas.py    # Pydantic 请求/响应模型
└── routes.py     # /agents, /agents/{id}/draft, /harness-profiles, /harnesses/*
```

路由在 `app/api/main.py` 通过 `api_router.include_router(...)` 注册。Alembic 迁移新增三张表。

### 依赖关系

- `llm_configs`（只读）：`AgentDraft` 引用 `provider_config_id` + `model_id`，校验时确认配置已启用、模型在 `llm_provider_model.is_enabled`。
- `runtime_management`（只读）：校验时读取 namespace 内 `RuntimeProfile` 上报的 CLI/SDK 版本（v0.4 已有 `RuntimeNode.sdk_version`、`agent_version` 等字段），用于 Harness 版本约束兼容性预检。
- `namespaces`（只读）：`X-Namespace-Id` + Admin/Developer 权限，复用 `require_namespace_admin` / `require_namespace_runtime_user`。

## 3. 数据模型

### 3.1 `agent_definition`

Agent 稳定身份表。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | uuid PK | |
| `namespace_id` | uuid FK→namespace, CASCADE | 所属空间 |
| `slug` | varchar(128) | 空间内唯一、创建后不可修改 |
| `name` | varchar(255) | |
| `description` | text | |
| `status` | enum `active/archived` | |
| `created_by` | uuid FK→user, SET NULL | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

约束：`UniqueConstraint(namespace_id, slug)`。

### 3.2 `agent_draft`

Agent 当前可编辑草稿，每个 Agent 恰好一个。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | uuid PK | |
| `agent_id` | uuid FK→agent_definition, CASCADE, UNIQUE | 1:1 |
| `revision` | integer | CAS 单调修订号，从 1 起 |
| `harness_profile_id` | uuid FK→harness_profile, SET NULL | 当前 Harness Profile |
| `provider_config_id` | uuid FK→llm_provider_config, SET NULL | 当前空间模型供应商配置 |
| `model_id` | varchar(255) | Agent 期望模型 |
| `system_prompt` | text | 系统提示词；不得含 secret 模板值 |
| `config` | json | schema 校验后的非敏感结构化覆盖项（超时、工作目录策略等） |
| `validated_revision` | integer nullable | 最近通过完整校验的 revision |
| `validation_result` | json | error/warning 代码及定位，不含密钥/远端原始响应 |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

创建 Agent 时自动创建 revision 1 的空草稿。

### 3.3 `harness_profile`

可复用 Harness/CLI 配置。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | uuid PK | |
| `namespace_id` | uuid FK→namespace, CASCADE | |
| `name` | varchar(255) | 空间内唯一 |
| `harness_type` | varchar(64) | v0.5 仅 `claude_code` 可执行；未知类型返回 unsupported |
| `config_schema_version` | varchar(32) | Harness adapter 配置 schema 版本 |
| `cli_version_constraint` | varchar(128) | CLI 兼容版本约束表达式 |
| `sdk_version_constraint` | varchar(128) | SDK 兼容版本约束表达式 |
| `config` | json | 结构化权限、超时、工作区及 allowlisted env 名 |
| `archived` | bool default false | 归档状态 |
| `created_by` | uuid FK→user, SET NULL | |
| `created_at` | timestamptz | |
| `updated_at` | timestamptz | |

约束：`UniqueConstraint(namespace_id, name)`。

### 3.4 `config` JSON 结构与安全校验

`harness_profile.config` 与 `agent_draft.config` 共享以下受 schema 校验的字段（非敏感 Setting）：

- `permission_mode`：枚举 `default / acceptEdits / plan / bypassPermissions`。`bypassPermissions` 在 schema 层直接拒绝，不接受。
- `timeout_seconds`：正整数，不得超过平台上限（常量 `MAX_TIMEOUT_SECONDS`，默认 3600）。
- `working_directory_strategy`：`inherit` 或 `require_root`，root 必须来自 runtime 预登记的 allowlist（本阶段只存策略名，root 校验在阶段五 Resolver）。
- `allowed_env_names`：字符串数组，仅允许 `ENV_ALLOWLIST` 中的名称。
- `builtin_tool_policy`：内置工具策略占位（本阶段只存结构，合并逻辑在阶段二/五）。

**schema 层硬拒绝**（不只靠前端隐藏）：`bypassPermissions`、任意含 Shell 元字符的字符串字段、`api_key/token/password/secret` 任意 key、Header value、denylisted env 名。

### 3.5 环境变量目录（`catalog.py` 常量）

不可绕过的 **denylist**（即使被误加入 allowlist 仍优先拒绝）：

```
LD_PRELOAD, LD_LIBRARY_PATH,
DYLD_INSERT_LIBRARIES, DYLD_LIBRARY_PATH, DYLD_FALLBACK_LIBRARY_PATH,
PYTHONPATH, PYTHONHOME, PYTHONSTARTUP,
NODE_OPTIONS, NODE_PATH,
PATH, SHELL, BROWSER,
GIT_SSH_COMMAND,
NEOMUA_* (前缀匹配，覆盖 NeoMua 自身配置路径变量)
```

**allowlist**（v0.5 初始集合，可由平台配置扩展）：

```
CLAUDE_CODE_*, ANTHROPIC_MODEL, ANTHROPIC_BASE_URL,
MAX_THINKING_TOKENS, BASH_DEFAULT_TIMEOUT_MS, BASH_MAX_TIMEOUT_MS,
MAX_MCP_OUTPUT_TOKENS, DISABLE_TELEMETRY, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
```

`GET /harnesses/environment-catalog` 返回 `{allowlist, reserved, denylist}`，denylist 永远优先。

## 4. 业务逻辑

### 4.1 Agent 生命周期

1. Admin 创建 Agent（name + slug + description），系统同事务创建 revision 1 空草稿。
2. slug 创建后不可修改（PATCH 接口忽略 slug 字段或拒绝）。
3. Agent 可标记 `active`/`archived`。归档后不可编辑、校验或创建新发布；已有发布和历史任务仍可读取（本阶段无发布，但归档阻断校验/保存草稿的规则仍生效）。
4. 从未发布的空 Agent 删除需二次确认（前端弹窗，后端校验无关联发布后物理删除草稿+定义）。已被发布引用的 Agent 不物理删除（本阶段无发布链路，但删除接口预留"存在 release 引用时返回 409"的检查点）。

### 4.2 草稿 CAS 并发控制

- `PUT /agents/{agent_id}/draft` 必须携带 `expected_revision`。
- 服务端 `SELECT ... FOR UPDATE` 锁定草稿行，仅在 `draft.revision == expected_revision` 时更新并 `revision += 1`。
- 冲突返回 409 + 当前最新 revision，不覆盖。
- 任何草稿字段变更后，`validated_revision` 若指向旧 revision 则置 null（旧校验失效）。

### 4.3 草稿校验（`POST /agents/{agent_id}/draft/validate`）

按 `error / warning` 返回结构化结果，只对指定 revision 有效。校验项：

1. **模型校验**：`provider_config_id` 属于当前 namespace 且 `enabled=true`；`model_id` 在该配置下 `llm_provider_model.is_enabled=true`。失效返回 error（`model_stale` / `model_not_found` / `cross_namespace_model`），不替换为其他模型。
2. **Harness 校验**：`harness_profile_id` 属于当前 namespace 且未归档；`harness_type == claude_code`，否则 error `unsupported_harness`。
3. **版本约束校验**：读取 namespace 内 runtime 上报的 CLI/SDK 版本（平台 runtime + 已连接节点），检查是否满足 `cli_version_constraint` / `sdk_version_constraint`。无目标上报版本时标记目标兼容性 `unknown`，记为 error（阻断发布），不按兼容处理。
4. **配置安全校验**：`config` 通过 schema 校验，拒绝 `bypassPermissions`/denylist env/secret value/Shell 字符串。违反返回 error。
5. **system_prompt 校验**：非空；检测疑似 secret 模板（`{{.*key.*}}`、`AKIA*`、`sk-ant-*` 等）记为 warning，不阻断但提示。
6. **可选能力未选**：如未绑定 Harness Profile 记为 warning。

校验通过后 `validated_revision = 当前 revision`，`validation_result` 保存结果。草稿再次保存后 `validated_revision` 自动失效（置 null）。

### 4.4 Harness Profile 管理

1. Admin 创建/编辑 Profile。`harness_type` 非 `claude_code` 时返回 error `unsupported_harness`（数据保留字符串类型但校验阻断）。
2. Profile 配置通过 `catalog.py` 的 schema 校验；`bypassPermissions`、未知 CLI flag、Shell 字符串、secret value、denylisted env 名拒绝保存。
3. 被 Agent 草稿或发布引用的 Profile 只能归档，不能删除（删除接口检查引用，存在引用返回 409）。
4. Profile 配置损坏或 schema 不兼容时拒绝新发布（本阶段无发布，但校验接口会反映该 error），不回退默认 Profile，不影响已激活 Release。

### 4.5 校验结果格式

```json
{
  "validated_revision": 3,
  "status": "error",
  "errors": [
    {"code": "model_stale", "field": "model_id", "message": "...", "location": "agent_draft.model_id"}
  ],
  "warnings": [
    {"code": "potential_secret_in_prompt", "field": "system_prompt", "message": "..."}
  ],
  "target_compatibility": [
    {"runtime_profile_id": "...", "runtime_type": "platform", "cli_version": "1.0.0", "sdk_version": null, "compatible": true},
    {"runtime_profile_id": "...", "runtime_type": "node", "cli_version": null, "sdk_version": null, "compatible": null, "reason": "unknown"}
  ]
}
```

## 5. API 设计

全部使用 `X-Namespace-Id`；mutation 在 Cookie 认证时继续执行 CSRF；响应不返回 secret value（本阶段 schema 层拒绝 secret 入库）。

| Method | Path | 权限 | 用途 |
|--------|------|------|------|
| GET | `/agents` | Admin/Developer | 列表（含草稿修订、校验状态） |
| POST | `/agents` | Admin | 创建 Agent（同时创建 revision 1 草稿） |
| GET | `/agents/{agent_id}` | Admin/Developer | 详情 |
| PATCH | `/agents/{agent_id}` | Admin | 修改 name/description（slug 不可改）；归档通过 status 字段 |
| DELETE | `/agents/{agent_id}` | Admin | 未发布空 Agent 删除（二次确认，后端校验无 release 引用） |
| GET | `/agents/{agent_id}/draft` | Admin/Developer | 读取草稿 |
| PUT | `/agents/{agent_id}/draft` | Admin | 按 `expected_revision` 保存草稿，CAS |
| POST | `/agents/{agent_id}/draft/validate` | Admin | 校验指定草稿 revision |
| GET | `/harness-profiles` | Admin/Developer | 列表 |
| POST | `/harness-profiles` | Admin | 创建 Profile |
| GET | `/harness-profiles/{id}` | Admin/Developer | 详情 |
| PATCH | `/harness-profiles/{id}` | Admin | 更新（被引用时不可改 harness_type） |
| DELETE | `/harness-profiles/{id}` | Admin | 归档/删除（被引用返回 409，只能归档） |
| GET | `/harnesses/catalog` | Admin/Developer | 支持的 harness 类型、schema 和安全字段目录 |
| GET | `/harnesses/environment-catalog` | Admin/Developer | 可配置 env 名、保留名、不可覆盖 denylist |

### 响应模型要点

- Agent 列表项含 `status`、`harness_type`（来自草稿引用的 Profile）、`model_id`、`draft_revision`、`validation_status`（derived: `unvalidated / validated / stale / error`）。
- 草稿响应含 `revision`、`validated_revision`、`validation_result`、`config`（非敏感）。
- Profile 响应含 `archived`、`referenced_by_agents`（布尔，提示能否删除）。
- harness catalog 返回 `{harnesses: [{type: "claude_code", config_schema_version, supported: true, fields: [...]}]}`。
- environment catalog 返回 `{allowlist: [...], reserved: [...], denylist: [...]}`，denylist 永远优先。

### 错误码约定

- 409：slug/name 重复、CAS expected_revision 冲突、删除被引用资源。
- 404：跨 namespace 访问或不存在（统一 404 避免跨空间探测）。
- 422：结构校验失败（schema 拒绝 secret/Shell/denylist、SemVer 等）。
- 403：Developer 写操作、User 任何访问。

## 6. 前端

### 6.1 路由

- `/system/agents`：Agent 列表
- `/system/agents/$agentId`：Agent 编辑器
- `/system/harnesses`：Harness 配置

侧边栏：Admin/Developer 显示"Agent 管理"组，下含 Agent 与 Harness 两个二级入口；User 不显示。

### 6.2 Agent 列表页

- 标题 + 创建按钮 + 状态筛选（active/archived）。
- 表格列：名称、slug、Harness、模型、草稿修订、校验状态、发布状态（本阶段固定"未发布"）。
- 操作：进入编辑、复制（新建 Agent 复制草稿）、归档。
- Developer 只读（无创建/编辑按钮）。
- 切换 namespace 清空旧空间查询缓存并重新读取。

### 6.3 Agent 编辑器页

- 顶部固定栏：名称、slug（只读）、revision 号、校验状态徽标。
- 五页签：
  1. 概览：name/description/status。
  2. 模型与提示词：provider_config 选择、model_id 选择（联动已启用模型）、system_prompt 编辑器。
  3. Harness：选择/创建 Harness Profile，展示其配置与版本约束。
  4. 能力：本阶段置灰"待后续阶段启用"。
  5. 校验与发布：本阶段置灰"待后续阶段启用"。
- 保存草稿显式按钮（不自动保存关闭时静默提交）；携带 `expected_revision`。
- 409 冲突时显示"已被其他人更新"，提供"重新加载"（丢弃本地，拉取最新），不自动覆盖或静默合并。
- 字段校验：name 必填、system_prompt 必填、模型与 Harness Profile 必选、超时为正整数且在平台范围。

### 6.4 Harness 配置页

- Profile 列表 + 创建/编辑抽屉。
- 抽屉字段：name、harness_type（v0.5 仅 claude_code，下拉禁用其他）、cli_version_constraint、sdk_version_constraint、permission_mode（排除 bypassPermissions）、timeout_seconds、allowed_env_names（多选，仅 allowlist）、working_directory_strategy。
- 目标兼容性预览：列出 namespace 内平台/节点 runtime 上报的 CLI/SDK 版本，逐项判断是否满足约束；unknown 单独标黄。
- 归档前展示受影响 Agent 列表；被引用时不提供物理删除按钮。

## 7. 安全不变式（实现必须满足）

1. `bypassPermissions` 不能通过任何接口保存（schema 层拒绝）。
2. denylist env 名不能通过 Profile 或 Agent Draft 注入，即使名称被误加入 allowlist。
3. `api_key/token/password/secret` 等 key 不能出现在 config JSON；Header value 不能保存。
4. Shell 元字符字符串字段拒绝保存。
5. 未知 `harness_type` 校验返回 `unsupported_harness` error，不伪装为 Claude 执行。
6. 跨 namespace 模型/Profile/Agent 访问统一返回 404。
7. 校验结果不含密钥或远端原始响应。
8. Worker、Node 和 Agent Draft API 均不能绕过统一 Resolver 自行生成执行配置（本阶段无执行入口，但草稿只表达配置意图，不生成执行配置）。

## 8. 测试计划（pytest）

`tests/agent_management/` 下覆盖：

1. **权限**：Admin 可 CRUD，Developer 只读（写操作 403），User 无页面/接口（403）。跨 namespace 访问 404。
2. **CAS**：两名 Admin 同时编辑同一 revision，后提交者 409，既有修改不被覆盖；草稿修改后 `validated_revision` 失效。
3. **slug**：同 namespace 重复 409；创建后 PATCH 不可改 slug。
4. **模型校验**：跨 namespace 模型 404/422；失效模型 `model_stale` error；不替换为其他模型。
5. **Harness**：未知 harness_type → `unsupported_harness` error；Profile 被 Agent 草稿引用时删除 409、只能归档。
6. **安全 schema**：`bypassPermissions` 保存被拒；denylist env 名保存被拒（即使名在 allowlist）；`api_key/token/password` key 保存被拒；Shell 元字符字符串保存被拒。
7. **环境目录**：`GET /harnesses/environment-catalog` 返回 allowlist/reserved/denylist；denylist 优先。
8. **harness 目录**：`GET /harnesses/catalog` 返回 claude_code 支持且 supported=true，其他类型 supported=false。
9. **归档**：归档 Agent 不可继续修改/校验（409）；归档 Profile 不影响已激活 Release（本阶段验证归档后新草稿引用被拒）。
10. **版本兼容性**：runtime 未上报版本时 target_compatibility 为 unknown 且记为 error。

## 9. 验收标准（对齐 PRD 第 7 节）

- [ ] Admin 可创建 Agent 与 Claude Harness Profile，Developer 只能读取，User 无页面和接口权限。
- [ ] 两名 Admin 同时编辑同一 revision 时，后提交者收到 409，既有修改不会被覆盖。
- [ ] 非当前 namespace 模型、失效模型和未知 Harness 均阻断校验，不自动替换。
- [ ] CLI 配置不能保存 `bypassPermissions`、任意 Shell、未知 flag 或 secret value。
- [ ] Loader/链接器、Python、Node、Shell、Git 和 NeoMua 保留环境变量不能通过 Profile 或 Agent Draft 注入。
- [ ] 目标未上报版本时显示 unknown 并阻断发布兼容性判断。
- [ ] 草稿修改后旧验证结果失效；归档 Agent 不可继续修改或发布。
- [ ] Profile 损坏或 schema 不兼容时拒绝新发布，不回退默认配置，也不影响当前已激活 Release。
- [ ] Worker、Node 和 Agent Draft API 均不能绕过统一 Resolver 自行生成执行配置。
- [ ] v0.5 的执行入口只接受 `harness_type=claude_code`。

## 10. 实现顺序

1. `app/agent_management/catalog.py`：harness 目录、env allowlist/denylist、config schema 校验函数。
2. `app/agent_management/models.py`：三张 SQLModel 表 + enums。
3. Alembic 迁移：新增三表 + enums + 唯一约束。
4. `app/agent_management/service.py`：CAS 保存、校验、Profile 安全校验。
5. `app/agent_management/schemas.py` + `routes.py`：8 组 API。
6. `app/api/main.py` 注册路由。
7. pytest 测试。
8. 前端：API client（手写 tenantApi 调用，与现有 `src/api` 模式一致）、三个页面 + 侧边栏。
9. 前端关键路径手测验证。
