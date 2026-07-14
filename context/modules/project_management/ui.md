# project_management UI

`/projects` 提供项目目录，标题区“新建项目”打开完整创建弹窗，包含名称、唯一标识、默认 Runtime、初始成员和可选仓库；交互方式与 Items 列表一致，不直接在顶部栏塞入简化输入。`/projects/:projectId` 展示成员、仓库、Spec 位置/标准绑定和项目任务。

`/system/spec-standards` 通过标题区按钮打开完整创建弹窗，同时填写标准身份和首个不可变版本；页面统一使用“唯一标识”，不向业务用户展示 Slug 术语。
