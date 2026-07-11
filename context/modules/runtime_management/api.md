# Runtime Management API

所有浏览器接口使用 HttpOnly Cookie 会话、CSRF（mutation）与 `X-Namespace-Id`；非浏览器调用可使用 Bearer。admin 管理运行时、节点、凭证和内容分发；developer 读取、测试和下发普通任务；user 返回 403；停用 namespace 的业务入口返回 409。

平台 Worker 使用 `X-Runtime-Token` 领取任务、续租和回传事件。节点使用 protocol v2 出站 WSS；握手 JWT 同时绑定设备公钥签名。Gateway 使用 `/tasks/{task_id}/v1/messages`，并按 namespace/runtime/task/model 四维 scope 查询控制面路由；任务终态后路由立即失效。

敏感字段规则：注册令牌只返回一次；设备私钥永不上传；模型和制品密钥不进入浏览器响应；下载 token 绑定 deployment/node/artifact/storage key 和过期时间。
