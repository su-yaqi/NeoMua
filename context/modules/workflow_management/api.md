# workflow_management API

模板目录/启用、预检、执行配置、实例、节点 mutation、事件和外部状态证明接口见根级 `context/apis.md`。`GET/PUT /workflow-templates/{templateId}/versions/{versionId}/execution-configuration` 管理 namespace 级模板执行配置，PUT 使用 `expected_revision` 并原子创建不可变修订。

`GET/POST /workflow-instances` 支持项目和独立实例以及按模板筛选。新建实例只接受精确模板版本、任务名称和应用定义的业务输入，服务端冻结模板当前执行配置、Package digest、项目快照、节点 Runtime 及 Agent Release；不接受实例级资源覆盖。节点 mutation 使用 expected revision，任务创建和外部状态处理使用幂等键。
