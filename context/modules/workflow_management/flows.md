# workflow_management 流程

部署时校验 `workflow_apps/<slug>` 的 Manifest、有限 DAG、Schema、Validator、代码入口和前端组件并注册不可变版本。创建任务前解析 Runtime、Agent Release、仓库和 Spec 快照。引擎按拓扑推进 human/agent/code 节点，追加修订并传播上游变化；不确定外部状态必须人工举证后才能恢复。
