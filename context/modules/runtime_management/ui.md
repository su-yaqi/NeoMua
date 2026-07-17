# Runtime Management UI

`/system/runtimes` 由 Runtime Instance 库存、Node 表、实例详情面板、签名内容列表和 Skill 同步矩阵组成。实例面板展示 engine/location、desired/applied 配置、能力报告、模型 Binding 与验证状态；旧平台/节点 Runtime Profile 配置卡不再是 v0.9 正式入口。

任务、节点、制品与发布详情使用非嵌套文件路由，保证列表组件不会遮蔽详情组件。发布详情对 Admin 提供失败/过期部署重试及已应用版本回滚；Developer 只读。

节点表展示在线状态、主机、系统和版本，支持 admin 注册/配置/吊销以及 admin/developer 普通任务下发。任务详情展示不可变快照与事件时间线。内容 UI 支持 admin 上传 ZIP、选择节点发布和查看每节点状态。

Runtime Instance 和节点详情按 Skill 展示 current desired、实际 applied、generation、状态、最后同步时间与脱敏错误；desired/applied 不一致明确标为 pending/syncing/failed/blocked。Admin 只可对 failed 目标显式重试。任务详情展示 `agent_task_skill_usage`、不可变快照和经过本地复核的逐次模型调用记录，包括调用/事件序号、Binding、usage 与状态；本地证据不匹配的任务会在首次模型调用前拒绝。

权限控件仅改善体验，后端依赖始终执行同样的 namespace 角色校验。
