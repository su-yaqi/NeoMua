# conversation_management 流程

Chat 仅向当前配置修订冻结的 exact Runtime Model Binding 发消息，不装配 Agent 能力。Agent 会话使用当前修订的参与者集合与唯一组织 Agent；每个参与者冻结 exact binding 或在保存时唯一解析的 Agent 偏好。用户可发给组织 Agent、指定 Agent 或全体；参与者变化只影响配置更新后的新消息。每个底层 `agent_task`、模型选择来源、委派输入/结果和失败均应可追溯。

项目与 Runtime Instance 在首个会话创建时确定且不可更换；需要不同项目或 Runtime 时创建新会话。项目会话在创建/刷新时追加 commit、Spec 版本和内容引用摘要快照，模型/Agent 修订和上下文刷新都不改写历史消息。
