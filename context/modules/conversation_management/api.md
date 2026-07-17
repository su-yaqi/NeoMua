# conversation_management API

目录、会话、消息、委派、附件和上下文接口见根级 `context/apis.md`。创建、派生、消息和委派 mutation 使用幂等键；附件仅接受最大 5 MiB、通过 MIME/扩展名/UTF-8/控制字节检查的 TXT、Markdown 与 JSON，不向 API 暴露存储路径。

会话创建后固定项目与 Runtime Instance。`POST /conversations/{id}/configuration-revisions` 使用 `expected_revision` 追加配置修订：Chat 选择 exact Runtime Model Binding；Agent 参与者选择 exact binding 或严格的 `agent_preference`，并指定其中唯一一个组织 Agent。存在在途轮次、目标未激活、模型不可用/歧义或 CAS 冲突时返回明确错误，不隐式应用部分配置。每条消息保存实际使用的 `configuration_revision_id` 和冻结执行证据。

`conversation_agent` 以参与者实例 ID 区分角色，不再按 `(conversation_id, agent_id)` 限制唯一；同一 Agent/Release 可作为多个独立角色出现。配置更新在同一 Release 存在多个参与者时要求提交 `conversation_agent_id`，避免歧义修改。
