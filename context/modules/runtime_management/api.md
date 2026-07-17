# Runtime Management API

所有浏览器接口使用 HttpOnly Cookie 会话、CSRF（mutation）与 `X-Namespace-Id`；非浏览器调用可使用 Bearer。admin 管理运行时、节点、凭证和内容分发；developer 读取、测试和下发普通任务；user 返回 403；停用 namespace 的业务入口返回 409。

平台 Worker 使用 `X-Runtime-Token` 领取任务、续租和回传事件。节点使用 protocol v2 出站 WSS；握手 JWT 同时绑定设备公钥签名。Gateway 使用 `/tasks/{task_id}/v1/messages`，并按 namespace/runtime/task/model 四维 scope 查询控制面路由；任务终态后路由立即失效。

敏感字段规则：注册令牌只返回一次；设备私钥永不上传；模型和制品密钥不进入浏览器响应；下载 token 绑定 deployment/node/artifact/storage key 和过期时间。

## Runtime Instance 与模型

- `/runtime-instances` 和 `/runtime-instances/{id}` 读取一对多 Node/Instance 库存、desired/applied 配置、当前能力报告及模型目录。
- `PUT /runtimes/{id}/configuration` 使用 expected revision 创建不可变配置；`POST .../configuration/apply` 触发平台 Worker 或 Node 应用。
- `/llm/model-definitions` 保存稳定模型身份；`/runtimes/{id}/model-bindings` 保存具体 Runtime 上的 provider/runtime-native 路由，验证通过后才进入执行目录。
- 新 Task 必须选择 exact binding 或把 Agent 偏好唯一解析到该 Runtime 的 Binding，并冻结配置、能力、模型目录和 effective spec 摘要。

Runtime Worker 与 Node Runtime 从本地 applied 配置、实际 Adapter、能力缓存和已验证模型路由生成 `model_evidence`，控制面精确核对后才允许首次模型调用。环境变量按运维级 `NEOMUA_RUNTIME_ENV_ALLOWLIST` 与配置 allowlist 的交集下传，应用 secret 和模型凭据始终排除；工作目录、权限、Tool/MCP、能力、超时和模型路由在执行边界复核。当前无法可靠执行的隔离、restricted network、CPU、内存、并发或未知策略会使配置应用失败，不会降级。

平台 Worker 和 Node 每 20 秒上报本地能力事实；Node 离线、报告超过 5 分钟、配置/能力指纹变化、Provider/模型停用或验证到期都会使 Binding 从目录、Activation precheck 和任务创建中失效。

## Skill 同步与使用

- `GET /skills/{skill_id}/runtime-sync` 与 `GET /runtimes/{runtime_id}/skills` 向 admin/developer 返回 desired/applied、generation、订阅数、状态和脱敏错误。
- Admin 通过 `/runtime-skill-states/{state_id}/retry` 或 `/skills/{skill_id}/runtime-sync/{runtime_id}/retry` 显式重试 failed 状态。
- 平台 Worker 使用 `/internal/runtime/skill-sync/claim|{attempt_id}/download|{attempt_id}/result`；节点通过设备 WSS 取得 desired/commit 消息，并使用 `/node/skill-sync/{attempt_id}/download` 的短期目标令牌。
- Runtime 通过 `/internal/runtime/skill-sync/tasks/{task_id}/usage` 上报任务绑定证据；缓存缺失或损坏通过 `.../preparation-failed` 阻断任务。所有内部接口继续要求独立服务凭证或设备身份。
