# Agent Management Flows

## 能力到 Release

1. Admin 创建 Agent 与 Claude Harness Profile，并以 CAS revision 保存草稿。
2. Skill ZIP 经路径、类型、大小和 executable 扫描后生成不可变 Version；Plugin 与 MCP 同样发布精确版本。
3. MCP target 写入平台密文或节点本地 `secret_ref`，在具体 Runtime 上验证并冻结限定名 Tool Schema/digest。
4. 统一 Resolver 合并 Profile、Agent、Skill、Plugin、Tool 与 MCP；权限只能收紧，冲突携带来源链，缺失能力不 fallback。
5. 校验成功的指定 draft revision 生成 canonical ResolvedAgentSpec、dependency lock、物化 manifest 和平台签名 Agent Release。

## Activation 与任务

1. Admin 选择明确 Runtime targets，先调用正式 precheck；不兼容目标返回诊断。
2. Activation 为每个 target 创建独立 deployment。平台 Worker 或节点 staging、验签、核对 digest 后原子切换 `(runtime, agent)` current pointer。
3. 多目标允许 `partial`，由 Admin 显式 retry 或 rollback；回滚创建新审计动作，只影响后续任务。
4. 新 Session/Task 只能引用 target 当前 active Release，并冻结相同 Resolved Spec digest；执行中不热替换能力。
5. `require_approval` Tool 在完全一致的参数摘要获批前保持等待，过期/断线默认拒绝。

## 节点 MCP secret

`neomua node secret` 只在存在 Node identity 的本机操作 OS credential store。控制面只接收 ref/fingerprint；指纹变化或移除后，心跳把相关 target 标记 `stale`，必须重新校验后才能用于新激活。
