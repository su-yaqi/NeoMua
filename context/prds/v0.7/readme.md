# v0.7 PRD

## 版本说明

- 版本：v0.7
- 状态：已完成
- 创建时间：2026-07-13
- 完成时间：2026-07-14

## 本版本目标

将 AI 工作台收敛为符合常见 AI Chat 使用习惯的 Chat/Agent 会话界面，把 Workflow 迁移到独立的用户侧导航入口并支持按模板选择项目上下文或独立执行，完善大模型接入配置在保存前的连接校验与模型同步体验，并统一管理页面创建顶层实体的交互方式。

## 文件组织

| 文件 | 说明 |
|------|------|
| `ai-chat-workspace.md` | 用户在双栏工作台中使用模型或可动态调整的多 Agent 会话 |
| `workflow-standalone-entry.md` | 空间成员从独立 Workflow 菜单进入已启用流程应用 |
| `llm-provider-draft-preflight.md` | 管理员在保存大模型接入配置前校验连接并同步模型预览 |
| `management-entity-creation-dialogs.md` | 管理员通过页面标题区按钮和弹窗创建项目、Skill、MCP、Plugin 与 Spec 标准 |
| `agent-capability-complete-creation.md` | 管理员在一个创建流程中完成 Agent、Skill、MCP 与 Plugin 的必要初始配置 |
| `project-spec-complete-creation.md` | 管理员创建项目时完成基础配置，创建 Spec 标准时同时发布首个不可变版本 |
| `user-facing-identifier-terminology.md` | 用户界面以“唯一标识”等业务名称替代难以理解的 Slug 文案 |
| `project-optional-workflow-execution.md` | 空间成员按模板依赖选择关联项目或独立执行流程 |
| `workflow-application-instance-list.md` | 用户在统一应用外壳中切换流程应用并管理实例 |
| `workflow-agent-role-binding.md` | 已被模板执行配置替代的实例级 Agent 角色绑定原方案 |
| `workflow-template-execution-configuration.md` | 管理员在模板中配置项目与节点 Runtime/Agent，业务用户只创建名称与目标明确的任务 |
| `web-platform-development-workflow.md` | 项目成员通过多 Agent Web 平台开发流完成需求、开发、测试、验收与上线 |
