# context 目录说明

本目录是 NeoMua 项目的状态文档工作区，用来沉淀当前代码已实现的能力、架构约束和历史版本快照。

## 目录结构

```text
context/
├── readme.md
├── project.md
├── architecture.md
├── data-schema.md
├── apis.md
├── ui.md
├── prds/
│   ├── v0.1/
│   ├── v0.2/
│   ├── v0.3/
│   ├── v0.4/
│   ├── v0.5/
│   ├── v0.6/
│   ├── v0.7/
│   ├── v0.8/
│   ├── v0.9/
│   └── v0.10/
├── changelogs/
│   ├── v0.1.md
│   ├── v0.2.md
│   ├── v0.3.md
│   ├── v0.4.md
│   ├── v0.5.md
│   ├── v0.6.md
│   ├── v0.7.md
│   ├── v0.8.md
│   ├── v0.9.md
│   └── v0.10.md
└── modules/
    ├── auth/
    │   ├── api.md
    │   ├── flows.md
    │   └── ui.md
    ├── users/
    │   ├── api.md
    │   ├── flows.md
    │   └── ui.md
    ├── items/
    │   ├── api.md
    │   ├── flows.md
    │   └── ui.md
    ├── namespaces/
        ├── api.md
        ├── flows.md
        └── ui.md
    ├── llm_configs/
    ├── runtime_management/
    ├── agent_management/
    ├── project_management/
    ├── conversation_management/
    └── workflow_management/
```

## 文件职责

- 根目录文件维护全局事实，反映当前分支代码的已实现状态与验收结论；未合并版本必须明确标记。
- `modules/` 目录维护各业务模块的接口、流程和页面细节。
- `prds/` 与 `changelogs/` 记录 v0.1-v0.10 的需求与开发快照；版本是否通过验收以对应 changelog 和审计记录为准，不能仅凭测试通过推断完成。

## 使用约定

- 当前实现优先以代码为准，文档用于帮助快速理解，不替代源码。
- 若新增业务模块，先更新 `project.md` 和 `architecture.md`，再补对应模块文档。
- 若只是扩展既有模块，优先更新 `modules/*`，必要时同步根级索引。
