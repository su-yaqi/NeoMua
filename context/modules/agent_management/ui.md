# Agent Management UI

- `/system/agents`：Agent 列表、复制、草稿/Harness/模型、精确能力绑定、校验与 Release。
- `/system/skills`、`/system/tools`：声明式 Skill Version 与 Tool 风险/策略目录。
- `/system/mcp-servers`：Revision、平台/节点 target、凭证写入、校验/Tool 快照、Runtime 事件与显式重启。
- `/system/plugins`：声明式 contribution 草稿、依赖验证、签名 Version 与 deprecate。
- `/system/agents/:agentId/releases/:releaseId`：Resolved Spec、依赖锁、签名、来源链、目标选择与 precheck。
- `/system/agent-activations/:activationId`：逐目标状态、错误、Retry 和 Rollback。
- `/system/runtimes` 与节点详情：按 Agent 展示 current Release 矩阵，并显示节点 MCP executable 与本地 secret_ref 就绪状态。

Admin 可管理；Developer 只读；User 不显示入口且后端返回 403。凭证输入不回显，页面只显示固定掩码、ref 是否存在和不可逆指纹状态。

Agent、Skill、MCP 与 Plugin 列表均参考 Items：在页面标题区提供明确的新建按钮，通过弹窗或独立页面完成全部必要初始配置，再提交一次原子创建。页面标签使用“唯一标识”，不展示 Slug 术语；详情页仍保留后续版本、Revision、校验、发布与激活操作。
