# Runtime Management API

所有浏览器接口使用 HttpOnly Cookie 会话、CSRF（mutation）与 `X-Namespace-Id`；非浏览器调用可使用 Bearer。admin 管理运行时、节点、凭证和内容分发；developer 读取、测试和下发普通任务；user 返回 403；停用 namespace 的业务入口返回 409。

平台 Worker 使用 `X-Runtime-Token` 领取任务、续租和回传事件。节点使用 protocol v2 出站 WSS；握手 JWT 同时绑定设备公钥签名。Gateway 使用 `/tasks/{task_id}/v1/messages`，并按 namespace/runtime/task/model 四维 scope 查询控制面路由；任务终态后路由立即失效。

敏感字段规则：注册令牌只返回一次；设备私钥永不上传；模型和制品密钥不进入浏览器响应；下载 token 绑定 deployment/node/artifact/storage key 和过期时间。

## Runtime Instance 与模型

- `GET /runtimes` 幂等确保每个 namespace 存在唯一 `platform_builtin` Claude Agent SDK，并读取 service/client Node 上报的实例。
- 平台、service 和 client Runtime 的配置由 `system_builtin`、`service_manifest` 或 `client_adapter` 生成，浏览器配置写入返回冲突；旧手工记录仅保留历史语义。
- `/llm/model-definitions` 保存稳定模型身份；`/runtimes/{id}/model-bindings` 保存具体 Runtime 上的 provider/runtime-native 路由，验证通过后才进入执行目录。
- LLM 配置成功后，平台自动对启用模型执行最小调用；只有模型调用证据和当前 Runtime 能力指纹同时有效，自动 Binding 才为 available。
- `GET /llm/provider-configs/{id}/runtime-readiness` 分别返回 Worker 发行信任、持久协调任务、模型真实调用和 Binding 就绪状态；service Runtime 使用同一模型验证，但 Binding 固定其自身当前能力指纹。
- 新 Task 必须选择 exact binding 或把 Agent 偏好唯一解析到该 Runtime 的 Binding，并冻结配置、能力、模型目录和 effective spec 摘要。

## service/client bootstrap

- Admin 通过 `/runtimes/nodes/bootstrap-sessions` 选择 `service` 或 `client`，明文凭证只返回一次。
- 平台运维先通过 `/runtime-node-distributions/adapters` 发布不可变签名 Adapter，再通过 `/runtime-node-distributions` 发布包含逐文件摘要的 Linux/systemd ZIP；client 发行必须精确引用一组 Claude Code/Codex Adapter release。
- 安装器通过 `/node/bootstrap/preflight` 绑定设备公钥与 Linux/架构事实，并验证平台签名的精确 Node Manager、Claude Agent SDK 或 Adapter Registry 清单。
- `/node/enroll` 要求模式一致和 Ed25519 设备证明；响应传输中断时仅 `/node/bootstrap/recover` 接受同一 bootstrap、同一公钥的恢复证明。
- service Node 必须发现且只发现一个受管 `claude_agent_sdk`；client Node 禁止报告该 SDK，只能通过签名 Registry 发现本机 Claude Code/Codex Adapter。
- `/node/bootstrap/receipt` 接受设备签名的安装回执；`/node/bootstrap/stage` 追加激活/失败 attempt。`repair` 默认只读，只有显式恢复同一服务 attempt 才修改状态，绝不重新 enroll。
- client 完整发现 generation 由设备签名，控制面核对 Registry/Adapter release/稳定 installation UUID 后创建实例；`/runtime-nodes/{id}/discovery/refresh` 仅请求下一代有限探测，`.../discovery-observations` 只返回摘要与结构化诊断。
- `/runtimes/{id}/pause|resume` 对 service/client 记录控制决策；恢复必须重新满足当前 receipt、在线能力与模型 Binding 证据。

Runtime Worker 与 Node Runtime 从本地 applied 配置、实际 Adapter、能力缓存和已验证模型路由生成 `model_evidence`，控制面精确核对后才允许首次模型调用。环境变量按运维级 `NEOMUA_RUNTIME_ENV_ALLOWLIST` 与配置 allowlist 的交集下传，应用 secret 和模型凭据始终排除；工作目录、权限、Tool/MCP、能力、超时和模型路由在执行边界复核。当前无法可靠执行的隔离、restricted network、CPU、内存、并发或未知策略会使配置应用失败，不会降级。

平台 Worker 和 Node 每 20 秒上报本地能力事实；Node 离线、报告超过 5 分钟、配置/能力指纹变化、Provider/模型停用或验证到期都会使 Binding 从目录、Activation precheck 和任务创建中失效。

## Skill 同步与使用

- `GET /skills/{skill_id}/runtime-sync` 与 `GET /runtimes/{runtime_id}/skills` 向 admin/developer 返回 desired/applied、generation、订阅数、状态和脱敏错误。
- Admin 通过 `/runtime-skill-states/{state_id}/retry` 或 `/skills/{skill_id}/runtime-sync/{runtime_id}/retry` 显式重试 failed 状态。
- 平台 Worker 使用 `/internal/runtime/skill-sync/claim|{attempt_id}/download|{attempt_id}/result`；节点通过设备 WSS 取得 desired/commit 消息，并使用 `/node/skill-sync/{attempt_id}/download` 的短期目标令牌。
- Runtime 通过 `/internal/runtime/skill-sync/tasks/{task_id}/usage` 上报任务绑定证据；缓存缺失或损坏通过 `.../preparation-failed` 阻断任务。所有内部接口继续要求独立服务凭证或设备身份。
