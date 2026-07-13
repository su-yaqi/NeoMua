# v0.5 PRD

## 版本说明

- 版本：v0.5
- 状态：已完成
- 创建时间：2026-07-12
- 完成时间：2026-07-13

## 本版本目标

在 v0.4 已完成的模型路由、Claude Agent Runtime、节点连接、可靠任务和签名内容分发之上，建立正式的 Agent Harness 管理面，使 Agent、Skill、Tool、MCP 和 Plugin 具备 namespace 隔离、版本、依赖、校验、确定性装配、发布、激活、审批和回滚生命周期，并提供复用正式 API 的 NeoMua Operator CLI。

## 已确认的产品边界

- v0.5 仅以 Claude Agent SDK / Claude Code CLI 为可执行 Harness；数据模型通过 `harness_type` 和 adapter schema 为未来 Harness 扩展预留边界。
- Plugin 使用 NeoMua 通用组合模型，并通过显式 `harness_type` 适配具体 Harness。
- Agent Release 同时支持平台运行时和节点运行时。
- Tool 管理只覆盖 Claude 内置 Tool、MCP 发现 Tool 及授权/审批策略，不开放任意可执行 Tool 上传。
- “Harness CLI”指 Claude Code CLI 的结构化配置、版本约束、兼容性和就绪状态；“Operator CLI”指 NeoMua 自身的管理命令行，两者是独立能力。
- 不远程安装或升级 Claude Code CLI、SDK、MCP executable 或 NeoMua 二进制。
- Skill v0.5 只接受声明式文档和批准的静态资源，不携带或引用可执行脚本。
- Plugin v0.5 是声明式“能力包”，不加载进程内代码、Tool handler 或生命周期 Hook。
- Agent Release 必须包含统一 Resolver 生成的 canonical `ResolvedAgentSpec`；发布、激活、任务不能分别拼装配置。
- Session 固定 Release、system prompt、Tool Schema 和 MCP Tool digest，不在多轮中途热替换。
- 任何不兼容、依赖冲突、凭证缺失或目标状态不明都明确阻断，不通过删除能力、放宽权限、切换模型或其他静默降级继续执行。

## 子需求与依赖

| 文件 | 用户目标 | 前置条件 |
|------|---------|---------|
| `namespace-agent-harness-management.md` | 管理 Agent 草稿及 Claude Harness/CLI 配置 | v0.4 Runtime、LLM Configs |
| `namespace-skill-tool-management.md` | 管理 Skill 版本、Tool 目录和授权策略 | Agent 草稿、Runtime 能力上报 |
| `namespace-mcp-server-management.md` | 管理 MCP Revision、目标凭证、校验和 Tool 发现 | Runtime 平台/节点通道 |
| `namespace-plugin-management.md` | 将 Skill/MCP/Tool 组合为不可变 NeoMua Plugin Version | 前述能力目录 |
| `namespace-agent-runtime-assembly.md` | 将多来源配置确定性解析、物化并执行 Tool 审批 | Agent/Harness 与能力目录 |
| `namespace-agent-release-activation.md` | 生成 Agent Release 并激活到平台/节点、绑定任务 | 以上全部子需求 |
| `neomua-operator-cli.md` | 通过正式 API 和本地安全凭证执行管理与自动化操作 | Auth 与全部正式管理 API |

```text
Agent/Harness ─┐
Skill/Tool ────┼─> NeoMua Plugin ─> ResolvedAgentSpec ─> Agent Release
MCP ───────────┘                                      │
                                                     ▼
Operator CLI ──> 正式 API ───────────────> Platform/Node Activation ─> Session/Task
```

## 建议实施阶段

1. 建立 `agent_management` 模块骨架、Agent/Harness 数据模型、权限依赖和管理页。
2. 实现 Skill 不可变版本、Tool catalog/策略及 Agent 草稿能力绑定。
3. 实现 MCP Revision、平台密钥、节点本地 secret_ref、目标校验、Runtime Manager 与 Tool 快照。
4. 实现 NeoMua Plugin 草稿、声明式 contribution、冲突解析、manifest 签名和不可变版本。
5. 实现 Resolver、`ResolvedAgentSpec`、Claude Adapter、Capability Catalog、Prompt Cache 不变式和 Tool 审批。
6. 实现 Agent Release 构建、平台/节点原子激活、任务/session 绑定及显式回滚。
7. 实现 Operator CLI profile、CLI session、命令注册、正式 API 复用和节点本地 secret 命令。

每个阶段必须通过迁移、API/CLI 权限、数据隔离、签名/密钥安全、目标失败恢复和 UI/CLI 关键路径验收后再进入下一阶段。不能用既有原始 ZIP 上传页面冒充上层管理能力，也不能让平台 worker 直接读取未发布草稿执行。

## 全版本验收主线

- Admin 从空白开始创建 Harness Profile 和 Agent 草稿，绑定精确 Skill、MCP 与 Plugin 版本，经同一 Resolver/Claude Adapter 完整校验后生成带 Resolved Spec digest 的不可变 Agent Release。
- 同一 Release 可分别激活到平台和已准备好的节点；不兼容节点单独失败，平台成功状态不被掩盖或回滚。
- Admin/Developer 只能从目标当前 active Agent 创建任务，任务详情可还原全部版本和策略，无法读取任何密钥明文。
- 更新 Skill、MCP、Plugin 或 Agent 草稿不会改变历史 Release；升级和回滚都需要显式操作并留下审计记录。
- require_approval Tool 在批准前不执行；无人审批、断线和超时不会自动放行。
- Session 中途发生 Tool Catalog、MCP Schema 或 Skill 变化时，当前 system prompt 和 Tool Schema 保持不变，后续激活明确 stale。
- Operator CLI 不保存 token/secret 明文，不直接访问数据库，所有 mutation 通过正式 API 和幂等键完成。
- 未安装 executable、缺少节点本地 secret、未知 CLI/SDK 版本、Tool 冲突和签名错误均阻断正式链路，不发生自动安装、静默降级或任意远程执行。
- 平台 worker 与节点统一通过结构化 `harness_capabilities` 上报 Claude CLI、SDK 和 Harness 实现版本；Node Agent 自身版本不参与 Claude 兼容性判断。目标不兼容只阻断该目标，至少一个兼容目标仍可完成 Agent 草稿验证。
