# conversation_management UI

`/workspace` 只提供 Chat 与 Agent：左侧为历史会话和“新会话”，右侧上方为历史消息、下方为输入框和配置控件。新建时选择项目、Runtime Instance，以及 exact 模型 Binding 或一个/多个 Agent；多 Agent 必须指定其中一个为组织 Agent，并为每个参与者显示 exact/agent-preference 模式及解析状态。

`/workspace/conversations/:conversationId` 中项目与 Runtime Instance 只读，模型可在当前 Runtime 的可用 Binding 间切换，Agent 可增减并更换组织 Agent；不可用或歧义偏好应禁止保存。用户点击“应用配置”后生成新修订。消息可发给组织 Agent、指定 Agent 或全体，完整委派记录可展开查看 delegation/task 标识、模型证据、输入、结果或错误。Workflow 已移至独立左侧菜单 `/workflows`。
