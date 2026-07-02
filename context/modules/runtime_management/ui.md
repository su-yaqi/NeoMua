# Runtime Management UI

`/system/runtimes` 由三块组成：固定平台运行时卡片、节点运行时表、签名内容列表。平台功能测试保持同一 Agent session 并通过鉴权 SSE 展示事件。

任务、节点、制品与发布详情使用非嵌套文件路由，保证列表组件不会遮蔽详情组件。发布详情对 Admin 提供失败/过期部署重试及已应用版本回滚；Developer 只读。

节点表展示在线状态、主机、系统和版本，支持 admin 注册/配置/吊销以及 admin/developer 普通任务下发。任务详情展示不可变快照与事件时间线。内容 UI 支持 admin 上传 ZIP、选择节点发布和查看每节点状态。

权限控件仅改善体验，后端依赖始终执行同样的 namespace 角色校验。
