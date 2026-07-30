# workflow_management UI

`/workflows` 是用户侧应用卡片目录；`/apps/:workflowSlug` 左上可切换应用，主体为实例列表，右上提供“新建流程实例”。`/apps/:workflowSlug/new` 与 `/apps/:workflowSlug/tasks/:instanceId` 由固定版本前端组件独立实现；组件缺失时明确阻断，不使用通用页面降级。

实例创建页只显示任务名称和“要做什么”。Web 平台开发流详情顶部显示可点击的流程步骤图和当前步骤，下方按页签独立展示 Agent 信息、产出物清单与预览、Agent 聊天窗口。

`/system/workflows` 管理已部署模板版本和启用状态；每个版本的“执行配置”页设置项目以及各节点 Runtime Instance、Agent Release 与模型模式。页面明确区分代码固定的节点逻辑与人工可配置的执行资源，并提示保存后只影响新实例；模型偏好不可用或歧义时禁止保存。
