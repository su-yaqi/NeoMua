# v0.10 PRD

## 版本说明

- 版本：v0.10
- 状态：实现完成，待代码评审与发布
- 创建时间：2026-07-18

## 本版本目标

将 Runtime 从“用户创建并填写命令的通用执行配置”收敛为三种来源明确、生命周期不同的正式产品能力：平台内置 Runtime、服务节点 Runtime 与客户端 Runtime。平台内置 Runtime 在空间配置好大模型接入后即可使用；服务节点由管理员在目标机器执行正式命令安装；客户端 Runtime 由节点管理端自动发现和控制机器上已有的受支持引擎。

## 统一术语与边界

本文将需求中所称的“Claude ADK / Claude SDK”统一使用仓库现有官方包名 **Claude Agent SDK**。如果实现阶段确认目标并非 `claude-agent-sdk`，必须暂停并重新确认，不得以其他 SDK 或 CLI 代替。

运行时类型与执行引擎是两个不同维度：

| Runtime 管理类型 | 位置 | 生命周期所有者 | 引擎来源 |
|---|---|---|---|
| `platform_builtin` | 平台 | NeoMua 平台发布 | 平台内置 `claude_agent_sdk` |
| `service_managed` | Runtime Node | NeoMua 节点安装清单 | bootstrap 命令安装的 `claude_agent_sdk` |
| `client_discovered` | Runtime Node | 节点机器管理员/外部安装器 | 管理端发现的 Claude Code、Codex 等受支持引擎 |

统一约束：

- 业务用户不得填写 executable、安装标识、任意参数、环境变量或自定义适配器来创建上述 Runtime。
- 平台和服务节点的引擎由受签名发布清单确定；客户端引擎由受信任适配器发现，不按文件名猜测，不扫描整块磁盘。
- Runtime 目录继续返回精确 `runtime_instance_id`。Agent、Project、Conversation 和 Workflow 不按类型、名称、负载或排序自动替换目标。
- 发现、安装、认证、模型或能力验证失败时保持不可用并返回结构化诊断，不回退到另一 Runtime、模型或执行引擎。
- 历史 `RuntimeProfile`、Runtime Instance、配置修订、Task 和执行证据不删除、不改写。缺少可信来源证据的旧节点不自动猜测为服务节点或客户端节点。

## 文件组织

| 文件 | 说明 |
|---|---|
| `builtin-platform-runtime.md` | 平台自动提供内置 Claude Agent SDK Runtime，并从已验证的大模型配置自动形成路由 |
| `managed-service-node-runtime.md` | 管理员在目标节点执行正式 bootstrap 命令，事务化安装受管 SDK 与管理服务 |
| `client-runtime-discovery.md` | 节点管理端自动发现、验证并控制本机已有 Claude Code、Codex 等 Runtime |

## 实现顺序

1. 先落地统一 Runtime 管理类型、只读来源信息和迁移诊断。
2. 实现平台内置 Runtime 与 LLM 路由自动协调，删除平台 Runtime 的业务创建/配置入口。
3. 实现服务节点 bootstrap 的签名分发、事务化安装和自动注册。
4. 实现客户端管理端安装、受信任适配器发现和节点本地执行引用。
5. 最后统一调整 Agent、Project、Conversation、Workflow 的 Runtime 目录展示与筛选；所有消费者继续保存精确 Runtime ID。
