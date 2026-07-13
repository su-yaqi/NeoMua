# v0.6 PRD

## 版本说明

- 版本：v0.6
- 状态：已实现
- 创建时间：2026-07-13
- 基线：按产品决策假设 v0.5 已按 PRD 完成

## 本版本目标

在 v0.5 的模型网关、Runtime、Agent、Skill、Tool、MCP、Plugin、Release 与 Harness 管理能力之上，建立面向最终使用者的 AI 工作台、项目上下文和可插装业务流程体系。用户可以直接 Chat、使用单/多 Agent 圆桌会话，或在项目下通过独立 Workflow 应用创建并协作完成项目任务。

## 已确认的产品边界

- AI 工作台包含 `Chat / Agent / Workflow` 三种模式。
- Chat 只使用固定模型，不获得 Agent、Tool、Skill 或 MCP 能力。
- 多 Agent 会话由主 Agent 主持；用户可直接点名、质疑或追问任一协作 Agent。
- 会话可以不属于项目；引入项目时固定 commit、Spec 和内容摘要快照，刷新只追加快照。
- 项目关联 1 到 N 个 Git 仓库，不存在主仓库；仓库用途使用文字描述。
- 每个仓库可配置多个 Spec 位置；跨项目 Spec 标准使用不可变版本并由项目显式绑定、升级。
- v0.6 不从会话或任务自动提炼项目记忆。
- Workflow 由代码开发，不提供拖拽设计器；Package 随 NeoMua 正常构建、测试和部署。
- 每个 Workflow Template 有独立应用页面，并复用公共任务、节点、历史和审计组件。
- 流程图是有限 DAG，v0.6 节点类型固定为 `human/agent/code`，不允许任意循环或未知跳转。
- 节点执行统一使用 `run(context)`，可选 `validate_in/validate_out`，不区分首次执行和更新接口。
- 确认模式只有过程确认、结果确认和无需确认。
- Runtime 按“节点指定 > 任务指定 > 项目默认”解析；节点全部工作在解析后的 Runtime 完成。
- 界面称“项目任务”，数据实体为 `workflow_instance`；现有 `agent_task` 继续作为底层执行记录。
- 上游结果更新时，节点逐个追加修订；依赖旧输入的下游保留旧结果并进入待更新。自动节点自动重跑，需要人工的节点等待确认。
- Agent 节点更新沿用原多轮对话，由系统追加上游变更消息并生成新结果版本。
- 模板版本不可变；任务固定创建时版本，新模板版本只用于之后创建的新任务。
- Git 分支、修改、提交、测试、合并由具体 Workflow 节点显式实现，不是平台隐式行为。
- 任一能力、Runtime、Validator、外部副作用或 Git 状态不明时停止并暴露问题，不自动换目标、删除能力或静默降级。

## 子需求与依赖

| 文件 | 用户目标 | 前置条件 |
|------|---------|---------|
| `project-context-and-spec-standards.md` | 管理项目、成员、多仓库、多 Spec 位置和跨项目标准 | namespaces、Runtime 工作区 |
| `chat-and-multi-agent-workspace.md` | 使用纯模型 Chat 或主 Agent 主持的多 Agent 圆桌 | v0.5 Agent Release、Runtime、项目上下文 |
| `workflow-package-and-application-management.md` | 以统一 SDK 开发、测试和发布带独立页面的流程应用 | v0.5 Runtime/Agent、项目配置 |
| `collaborative-workflow-task-execution.md` | 在项目下创建任务并逐节点协作、确认、更新和恢复 | Workflow Package、项目、会话与 Runtime |

```text
Project / Repositories / Spec Standards
          │
          ├──────────────> Chat / Agent Workspace
          │                          │
          └──> Workflow Package ─────┼──> Workflow Instance（项目任务）
                                     │              │
Agent Release / Runtime ─────────────┘              └──> Node Revision / Agent Task / Artifact
```

## 建议实施阶段

1. 建立 `project_management`：项目、成员、仓库、Spec 位置、标准版本和 Runtime 访问校验。
2. 建立 `conversation_management` 与 AI 工作台：纯模型 Chat、固定上下文快照和个人/项目共享会话。
3. 接入单 Agent 与多 Agent 圆桌：参与 Agent 固定、点名发言、委派时间线和完整排障记录。
4. 建立 Workflow SDK、Package 校验器、注册表与模板独立应用外壳。
5. 建立 `workflow_instance` 状态机、节点修订、Gate、确认、跳过、执行轮次和产物模型。
6. 实现上游变更传播、自动节点拓扑推进、Agent 原会话更新和副作用幂等保护。
7. 完成各独立 Workflow 应用的端到端测试、并发、恢复、安全和不降级验收。

## 全版本验收主线

- Namespace Admin/Developer 创建项目，配置多个仓库及多个 Spec 位置，并为位置绑定精确跨项目标准版本。
- 普通成员可创建不关联项目的纯模型 Chat；会话固定模型且不能获得 Tool。
- 用户选择一个主 Agent 和限定协作 Agent 集合进行圆桌会话，可直接追问任一 Agent并展开完整协作过程。
- AI/开发者在 `workflow_apps/<slug>` 完成 Manifest、节点、Validator、Schema、独立页面和测试；失败 Package 不能注册。
- 项目成员从独立应用创建固定模板版本的项目任务，系统完成全部 Runtime、Agent、仓库和 Schema 预检。
- 过程确认、结果确认和无需确认节点按定义推进；自动链路遇到人工节点正常暂停。
- 修改已完成的上游节点时只追加节点修订，下游旧结果保留；无需确认节点自动重跑，人工节点逐个等待处理。
- Agent 节点在原对话接收上游变更消息并输出新结果版本，完整对话和底层 Agent Task 可追溯。
- Git 或其他外部副作用无法确认时停止并要求人工处理，不自动重复、force、换 Runtime 或忽略错误。
- 已完成任务只读，新模板版本只影响新任务；历史模板、节点、Gate、确认、产物和上下文快照均可还原。
