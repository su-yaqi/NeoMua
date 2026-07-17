# project_management API

正式接口见根级 `context/apis.md`。所有请求绑定 `X-Namespace-Id`；项目成员读取，namespace Admin/Developer 写。`POST /projects/complete` 在一个事务中创建项目身份、默认 Runtime Instance、初始成员及可选仓库；失败不留下空项目。项目无物理删除；仓库验证必须携带目标 Runtime Instance，返回其明确 workspace/commit 证明或可诊断失败。

`POST /spec-standards/complete` 同时创建标准身份和首个不可变版本。后续仍通过版本接口追加版本，不允许修改已发布内容。
