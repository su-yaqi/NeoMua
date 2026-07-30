# project_management 流程

Admin/Developer 在一次创建流程中填写项目名称、唯一标识、默认 Runtime Instance、初始成员及可选仓库配置；全部校验通过后事务提交。之后可按用途绑定多个仓库并选择明确 Runtime Instance 验证。每个仓库可配置多个仓库内 Spec 路径，各自绑定精确标准版本；升级先更新绑定并显式预览，不自动修改 Git。归档后全部配置只读。

Spec 标准创建时同时填写首个版本号与 Manifest，身份和不可变首版原子发布，不产生只有名称与标识、无法使用的标准空壳。
