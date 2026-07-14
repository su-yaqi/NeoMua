# conversation_management UI

`/workspace` 只提供 Chat 与 Agent：左侧为历史会话和“新会话”，右侧上方为历史消息、下方为输入框和配置控件。新建时选择项目、Runtime，以及模型或一个/多个 Agent；多 Agent 必须指定其中一个为组织 Agent。

`/workspace/conversations/:conversationId` 中项目与 Runtime 只读，模型可切换，Agent 可增减并更换组织 Agent；用户点击“应用配置”后生成新修订。消息可发给组织 Agent、指定 Agent 或全体，完整委派记录可展开查看 delegation/task 标识、输入、结果或错误。Workflow 已移至独立左侧菜单 `/workflows`。
