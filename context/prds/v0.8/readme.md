# v0.8 PRD

## 版本说明

- 版本：v0.8
- 状态：开发与集中验证完成
- 创建时间：2026-07-17

## 本版本目标

将 Skill 从“只能上传 ZIP 并绑定精确版本的受管制品”升级为可直接维护内容、发布版本并由 Runtime 持续同步的空间级能力资产。管理员通过文件工作台编辑和预览 Skill；Agent 与 Plugin 只声明使用哪些 Skill，不配置版本；Runtime 在任务链路之外同步当前发布版本，任务直接使用已应用的本地不可变内容。

## 核心原则

- Skill 草稿可编辑，已发布 Skill Version 保持不可变。
- `current_version_id` 表示控制面当前希望 Runtime 使用的发布版本，不按 SemVer 大小隐式推断。
- Agent、Plugin、Agent Release、Conversation 与 Workflow 均不锁定 Skill Version。
- Skill 发布、Runtime 同步和任务使用是三个边界清晰的行为。
- 发布阶段完成内容解析、完整校验和不可变 Bundle 生成。
- 同步阶段下载、验签、校验、缓存并原子切换本地版本；同一摘要只处理一次。
- 使用阶段只把本地已应用版本绑定到单次任务，不访问控制面、不解析归档、不复制内容。
- 单次任务固定实际使用的本地不可变版本；同一 Session 的后续任务可使用同步完成的新版本。
- 同步未完成或失败时继续保留上一已应用版本，并明确展示 desired/applied 差异；从未成功同步的必需 Skill 不允许静默缺省执行。

## 文件组织

| 文件 | 说明 |
|------|------|
| `skill-content-workspace-and-versioning.md` | Skill 列表、文件工作台、草稿、校验、不可变发布版本和当前版本切换 |
| `skill-binding-synchronization-and-usage.md` | Skill 身份绑定、Runtime 通知与轮询同步、内容缓存、任务使用和审计 |

## 实施依赖

1. 先完成 Skill 草稿、文件工作台、发布版本与 `current_version_id`。
2. 再将 Agent/Plugin 的 Skill 配置改为身份绑定，并升级 Agent Release Schema。
3. 最后接入 Runtime Skill 同步、初次激活门禁、本地缓存和任务使用证据。

## 测试安排

本版本需求与代码分批实现期间暂不执行测试；全部需求完成后统一进行后端、Runtime Worker、Node Runtime、前端和端到端测试。

2026-07-17 已完成集中验证：Alembic 从 `c4d8a2f7e106` 升级至 `e8a1c4b7d902`；后端 231 项测试、Runtime Worker 与 Node Runtime 40 项测试、Playwright 74 项端到端测试全部通过；前端生产构建、Ruff 与差异格式检查通过。
