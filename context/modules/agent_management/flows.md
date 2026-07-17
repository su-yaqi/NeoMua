# Agent Management Flows

## 能力到 Release

1. Admin 通过完整创建流程一次填写 Agent 身份、Harness、模型、系统提示词和初始配置；服务端原子创建 Agent 与草稿，后续以 CAS revision 保存。
2. Skill 在文件草稿中维护或从 ZIP 安全导入；当前 revision 经路径、类型、大小、Manifest 和能力要求校验后生成不可变签名 Version，并原子推进 current version。Plugin 与 MCP 仍发布精确版本或 Revision。
3. MCP target 写入平台密文或节点本地 `secret_ref`，在具体 Runtime 上验证并冻结限定名 Tool Schema/digest。
4. 统一 Resolver 合并 Profile、Agent、Skill identity、Plugin、Tool 与 MCP；权限只能收紧，冲突携带来源链，缺失能力不 fallback。
5. 校验成功的指定 draft revision 生成 canonical ResolvedAgentSpec、dependency lock、物化 manifest 和平台签名 Agent Release；Skill 条目只保存身份，不冻结版本。

## Activation 与任务

1. Admin 选择明确 Runtime targets，先调用正式 precheck；不兼容目标返回诊断。
2. Activation 为每个 target 创建独立 deployment，并为 Release 中的 Skill identity 建立 Runtime 订阅。平台 Worker 或节点先在任务链路之外同步 current Skill Bundle，staging、验签和核对 digest 后原子推进 applied；首次未就绪时 Activation 保持等待或明确失败。
3. 多目标允许 `partial`，由 Admin 显式 retry 或 rollback；回滚创建新审计动作，只影响后续任务。
4. 新 Session/Task 只能引用 target 当前 active Release，并冻结相同 Resolved Spec digest；每个 Task 另行绑定该 Runtime 当时已 applied 的 Skill Version，执行中不热替换，同一 Session 的后续 Task 可使用新版本。
5. `require_approval` Tool 在完全一致的参数摘要获批前保持等待，过期/断线默认拒绝。

## 节点 MCP secret

`neomua node secret` 只在存在 Node identity 的本机操作 OS credential store。控制面只接收 ref/fingerprint；指纹变化或移除后，心跳把相关 target 标记 `stale`，必须重新校验后才能用于新激活。

## 顶层能力完整创建

- Skill 新建时通过编辑器模式原子创建身份、草稿和至少 `SKILL.md`；也可使用兼容 ZIP 流程直接创建首个版本。
- MCP 新建时同时填写 transport/config、首个 Revision 和 Runtime 目标，不先创建空身份。
- Plugin 新建时同时填写 contribution 与依赖草稿，满足必要配置后一次提交。
- 任一步骤失败均事务回滚；页面保留输入和诊断，不改走旧的“先建名称再补配置”路径。

## Skill 发布、同步与使用边界

1. 发布只发生在控制面：解析并校验草稿，生成内容摘要、Manifest 摘要、签名和不可变 Bundle。
2. 同步由 Runtime Worker 周期轮询或节点 WSS 通知触发；相同摘要命中本地缓存时只验证并切换指针，失败保留旧 applied。
3. 使用只发生在单次任务准备阶段：Runtime 为任务创建指向本地 applied 内容的只读绑定并上报证据，不请求 current version，不下载、不解包、不解析。
