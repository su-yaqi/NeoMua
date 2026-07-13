# project_management API

正式接口见根级 `context/apis.md`。所有请求绑定 `X-Namespace-Id`；项目成员读取，namespace Admin/Developer 写。项目无物理删除；仓库验证必须携带目标 Runtime，返回其明确 workspace/commit 证明或可诊断失败。
