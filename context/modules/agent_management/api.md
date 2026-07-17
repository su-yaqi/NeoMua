# Agent Management API

所有空间接口要求 `X-Namespace-Id`。Admin 可 mutation，Developer 可读取运行时所需的脱敏状态，User 无权访问。创建/发布类 mutation 使用 CAS revision 或 `Idempotency-Key`。

- Agent：管理身份、CAS 草稿、稳定模型偏好和引擎中立执行策略；Skill 按身份绑定，Plugin/MCP 按精确版本或 Revision 绑定；正式校验并生成签名 Release。Harness 写接口在 v0.9 返回 410，历史数据只读。
- Skill/Tool：文件草稿工作台、ZIP 导入、安全扫描、不可变签名 Version、显式 current version、平台风险基线与只可收紧策略。
- MCP：不可变 Revision、明确 Runtime target、平台密文/节点 `secret_ref`、校验历史、Tool 快照和 Runtime 重启。
- Plugin：四类声明式 contribution、依赖图校验、精确 dependency lock 与签名 Version。
- Activation：只读 precheck、逐目标 deployment、显式 retry/rollback 和 `runtime_agent_release` 指针。
- Approval：对完全相同的 task revision、tool call、qualified name 和 args digest 作出有时效决定。

v0.7 完整创建接口：

| Method | Path | 原子创建内容 |
|---|---|---|
| POST | `/agents/complete` | Agent 身份与可继续编辑/校验的完整初始草稿 |
| POST | `/skills/complete` | 兼容 ZIP 创建：Skill 身份与首个已校验不可变版本 |
| POST | `/skills/complete/editor` | Skill 身份、单一草稿与初始文件 |
| POST | `/mcp-servers/complete` | MCP 身份、首个不可变 Revision 及明确目标配置 |
| POST | `/plugins/complete` | Plugin 身份与完整初始 Draft |

以上接口校验失败时整体回滚，不遗留只有名称和标识的顶层实体。API 继续使用 `slug` 作为稳定机器字段，UI 展示为“唯一标识”。

v0.8 Skill 正式接口：

- `GET /skills` 返回列表摘要；`GET /skills/{id}` 返回身份、草稿、当前版本、版本历史和同步摘要。
- `/skills/{id}/draft/files` 及文件子路径提供创建、读取、更新、移动和删除；`POST .../draft/import` 将安全 ZIP 导入草稿。
- `POST .../draft/validate` 对当前 revision 做完整校验；`POST .../draft/publish` 生成不可变签名版本并原子设为 current。
- `POST /skills/{id}/current-version` 显式切换或回滚 current，要求 `Idempotency-Key`；版本文件读取和 deprecate 位于 `/skills/{id}/versions/...`。
- `PUT /agents/{id}/draft/skills` 只接受 Skill identity 与 enabled；Plugin 草稿 Skill contribution 同样不接受版本锁定。

内部 claim/result API 只接受 `X-Runtime-Token`；节点 MCP 校验和 deployment 结果通过设备鉴权 WSS 回传。任意响应、manifest 和任务 snapshot 均不得包含 secret value。

v0.9 的 Agent Release 不固定 Runtime、engine 或供应商路由；Activation 目标必须是 Runtime Instance。Precheck 应同时验证在线状态、applied 配置、Adapter、能力/安全上限、Skill、MCP 和模型目录。当前开发快照缺少独立 compatibility API，且 precheck 对在线/过期状态和部分安全策略的验证不完整，发布前仍需整改。
