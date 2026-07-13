# conversation_management 流程

Chat 仅向固定 provider/model 发消息，不装配 Agent 能力。Agent 会话固定主 Agent 与协作 Agent 集，用户可发给主 Agent、指定 Agent 或全体；每个底层 `agent_task` 和失败均可追溯。项目会话在创建/刷新时追加 commit、Spec 版本和内容引用摘要快照。
