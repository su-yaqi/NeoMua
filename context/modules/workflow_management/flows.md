# workflow_management 流程

部署时校验 `workflow_apps/<slug>` 的 Manifest、有限 DAG、Schema、Validator、代码入口和前端组件并注册不可变版本。节点定义与实际执行属于代码层；Manifest 的 `project_mode` 声明项目必选、可选或禁用，Agent 节点用 `agent_role_key` 声明逻辑职责。

Admin/Developer 在模板版本配置页选择项目，并为每个非人工节点选择兼容 Runtime Instance；Agent 节点还需选择该 Runtime 上已激活的精确 Agent Release，并选择 exact 模型 Binding 或严格的 Agent 偏好模式。完整校验后保存为不可变修订。业务用户创建实例时只填写任务名称和“要做什么”，服务端冻结当前配置修订、模型选择证据与项目快照；之后模板配置变更不影响已有实例。

引擎按拓扑推进 human/agent/code 节点，追加修订并传播上游变化；不确定外部状态必须人工举证后才能恢复。内置 `Web 平台开发流` 依次包含需求沟通、需求设计、技术方案、后端开发、前端开发、测试用例、测试环境部署、测试、人工验收和上线。
