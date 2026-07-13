# Agent Management API

所有空间接口要求 `X-Namespace-Id`。Admin 可 mutation，Developer 可读取运行时所需的脱敏状态，User 无权访问。创建/发布类 mutation 使用 CAS revision 或 `Idempotency-Key`。

- Agent/Harness：管理身份与草稿、精确能力绑定、正式校验、签名 Release。
- Skill/Tool：ZIP 安全扫描、不可变 Version、平台风险基线与只可收紧策略。
- MCP：不可变 Revision、明确 Runtime target、平台密文/节点 `secret_ref`、校验历史、Tool 快照和 Runtime 重启。
- Plugin：四类声明式 contribution、依赖图校验、精确 dependency lock 与签名 Version。
- Activation：只读 precheck、逐目标 deployment、显式 retry/rollback 和 `runtime_agent_release` 指针。
- Approval：对完全相同的 task revision、tool call、qualified name 和 args digest 作出有时效决定。

内部 claim/result API 只接受 `X-Runtime-Token`；节点 MCP 校验和 deployment 结果通过设备鉴权 WSS 回传。任意响应、manifest 和任务 snapshot 均不得包含 secret value。
