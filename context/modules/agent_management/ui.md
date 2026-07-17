# Agent Management UI

- `/system/agents`：Agent 列表、复制、草稿、稳定模型偏好、引擎中立策略、Skill 身份与其他精确能力绑定、校验和 Release；不再编辑 Harness。
- `/system/skills`：外层 Skill 资产列表；详情为左侧文件目录树、右侧文本/Markdown 编辑与预览的内容工作台，并提供校验、发布、版本历史、current version 与 Runtime 同步状态。
- `/system/tools`：Tool 风险与策略目录。
- `/system/mcp-servers`：Revision、平台/节点 target、凭证写入、校验/Tool 快照、Runtime 事件与显式重启。
- `/system/plugins`：声明式 contribution 草稿、依赖验证、签名 Version 与 deprecate。
- `/system/agents/:agentId/releases/:releaseId`：Resolved Spec、依赖锁、签名、来源链、目标选择与 precheck。
- `/system/agent-activations/:activationId`：逐目标状态、错误、Retry 和 Rollback。
- `/system/runtimes` 与节点详情：按 Runtime Instance/Agent 展示 current Release 矩阵、模型 Binding，并显示节点 MCP executable 与本地 secret_ref 就绪状态。

Admin 可管理；Developer 只读；User 不显示入口且后端返回 403。凭证输入不回显，页面只显示固定掩码、ref 是否存在和不可逆指纹状态。

Agent、Skill、MCP 与 Plugin 列表均参考 Items：在页面标题区提供明确的新建按钮，通过弹窗或独立页面完成全部必要初始配置，再提交一次原子创建。页面标签使用“唯一标识”，不展示 Slug 术语。Skill 新建默认进入编辑器模式，至少创建 `SKILL.md`；Agent 与 Plugin 的 Skill 选择器不展示版本下拉，只显示 current、同步提示和 enabled 状态。Harness 页面只展示迁移历史，不提供新增、编辑或路由选择。
