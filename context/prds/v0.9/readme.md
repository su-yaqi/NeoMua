# v0.9 PRD

## 版本说明

- 版本：v0.9
- 状态：开发与集中验证完成
- 创建时间：2026-07-17

## 本版本目标

重构 Agent、Harness、Runtime 与模型之间的职责边界。机器继续由 Runtime Node 表示；一台机器可以承载多个 Claude Code、Codex 等 Runtime 实例。Runtime 成为实际执行 Agent Loop 的引擎实例，声明自身能力、受控配置和经过验证的模型绑定；Agent 只声明身份、行为、能力意图与模型偏好；Conversation、Agent 组和 Workflow 的不可变配置修订负责选择实际 Runtime 与模型，并在任务启动前完成严格校验和冻结。

## 核心原则

- Runtime Node 表示机器或执行位置，Runtime 表示该位置上实际运行 Agent Loop 的引擎实例，两者为一对多关系。
- Harness 是 Runtime 的内部引擎适配能力，不再作为 Agent 下的独立用户配置资源。
- Agent Release 保持引擎中立，只保存模型偏好、Tool/MCP/Skill 意图、执行策略和所需能力。
- Runtime 只声明经过发现、映射和验证的模型绑定；“支持”意味着该 Runtime 能通过确定路由实际调用该模型。
- Conversation、Agent 组和 Workflow 节点必须显式选择“具体模型”或“采用 Agent 偏好”，不设置隐含默认模型。
- `agent_preference` 只有在目标 Runtime 存在同一模型身份的可用绑定时才能解析成功；不允许相似名称、同系列模型或其他路由替代。
- 配置修订、Activation 和 Task 都记录实际 Runtime、引擎、模型、路由、能力指纹和选择来源，历史执行不随后续配置变化漂移。
- 所有不兼容、未验证、离线、能力缺失或迁移歧义均明确阻断；不静默切换 Runtime、模型、权限、Tool 或 Harness 行为。

## 文件组织

| 文件 | 说明 |
|------|------|
| `runtime-engine-and-model-capabilities.md` | Runtime Node 一对多 Runtime、引擎适配器、能力报告、模型目录与可执行模型绑定 |
| `agent-model-preference-and-runtime-activation.md` | 移除 Agent 侧 Harness 配置、Agent 模型偏好、策略归属、Release 兼容性与 Runtime 激活 |
| `execution-model-selection-and-task-freezing.md` | Chat、Agent 组、Workflow 和项目默认 Runtime 的模型选择、配置修订解析与任务冻结 |

## 实施依赖

1. 先建立 Runtime 实例、引擎适配器、稳定模型身份和 Runtime 模型绑定。
2. 再升级 Agent 草稿/Release Schema，迁移 Harness 字段并完成新 Activation 预检与绑定。
3. 最后升级 Conversation、Agent 组、Workflow、Project 和 Task，使实际 Runtime/模型选择进入不可变配置与执行证据。
4. 完成全量迁移预检后一次切换新写入路径；旧 Runtime Profile、Harness Profile、旧 Release 与历史 Task 仅保留只读兼容，不长期双写。

## 测试安排

三份需求可按实施依赖分批开发，但版本完成前统一执行数据库迁移、后端 API、Runtime Worker、Node Runtime、引擎适配器、前端与端到端测试。迁移必须先在生产数据副本执行只读预检；存在无法唯一映射的数据时停止切换并输出对象级诊断，不跳过、不猜测。

## 完成情况

v0.9 已按上述职责边界完成数据库、后端、Runtime Worker、Node Runtime、前端和端到端链路改造。数据库已升级至 `fb4e6f8a0b23`，Alembic 模型差异检查无待生成操作；Backend 210、Runtime Worker 与 Node Runtime 42、Playwright 74 项全部通过，前端生产构建、Python Ruff 和差异格式检查通过。详细内容见 `context/changelogs/v0.9.md`。
