# Runtime Management UI

`/system/runtimes` 按“平台内置 / 服务节点 / 客户端 / 历史 Runtime”分组展示库存、Node 表、实例详情、签名内容和 Skill 同步矩阵。平台内置 Runtime 明确显示“无需配置”；所有系统管理实例只读展示配置来源、Adapter 执行引用、能力报告和模型 Binding，不提供创建平台 Runtime、命令配置或手工 Binding 控件。

任务、节点、制品与发布详情使用非嵌套文件路由，保证列表组件不会遮蔽详情组件。发布详情对 Admin 提供失败/过期部署重试及已应用版本回滚；Developer 只读。

节点表展示在线状态、主机、系统、版本和 service/client 类型。Admin 分别使用“添加服务节点”“添加客户端节点”生成一次性凭证；页面说明由管理员登录目标主机执行、平台不会 SSH，并将无密文命令与隐藏输入凭证分开展示。Developer 不显示 bootstrap 与吊销控件。

节点详情展示管理模式、bootstrap 阶段、安装 receipt、发行/Registry 摘要和发现请求/已接收代次。client 节点允许 Admin 请求受控重扫并查看逐代 available/missing/blocked 观察；页面没有路径、命令或强制启用输入。service/client Runtime 可确认后暂停/恢复新任务，恢复失败直接显示证据未就绪，不跳过验证。

`/system/llm-providers` 在连接校验和模型同步之外显示平台 Runtime 就绪区：Worker 发行摘要、协调状态、每个模型真实调用、Binding 状态和精确错误相互独立。

Runtime Instance 和节点详情按 Skill 展示 current desired、实际 applied、generation、状态、最后同步时间与脱敏错误；desired/applied 不一致明确标为 pending/syncing/failed/blocked。Admin 只可对 failed 目标显式重试。任务详情展示 `agent_task_skill_usage`、不可变快照和经过本地复核的逐次模型调用记录，包括调用/事件序号、Binding、usage 与状态；本地证据不匹配的任务会在首次模型调用前拒绝。

权限控件仅改善体验，后端依赖始终执行同样的 namespace 角色校验。
