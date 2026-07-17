# Runtime Management Flows

## 节点注册与恢复

1. admin 创建 10 分钟一次性令牌。
2. 节点生成 Ed25519 密钥，只上传公钥；平台原子消费令牌并签发 90 天设备凭证。
3. 节点建立出站 WSS，每 20 秒心跳；超过 60 秒显示离线，但配对和凭证保留。
4. 关机一天后节点仍使用原身份重连，提交 spool、配置版本和中断任务对账。
5. 到期前 30 天轮换；节点原子保存新凭证、重连确认后平台吊销旧凭证。未确认时旧凭证只在 24 小时宽限内用于恢复轮换，超期由 maintenance 吊销。

## 任务执行

任务创建时冻结模型路由、模型、工具、目录 allowlist、权限模式、超时和执行器版本。同一 session 同时最多一个在途任务。控制面先按 connection generation 建立短期 reservation，再向节点发送；节点先把 `(task_id, revision)` 写入 SQLite 才确认调度。事件在任务行锁内原子写入并推进状态，相同序列/载荷幂等，不同载荷形成可诊断协议冲突。平台与节点超时都先 interrupt，grace 后取消并写入 `task_timeout`。租约过期只标记 interrupted，必须显式 retry。

## 内容分发

admin 上传 ZIP 前先取得数据库并发额度并检查临时目录容量；平台逐文件计算哈希、拒绝路径逃逸/符号链接并签署不可变清单。每个 `(node, logical_target)` 同时最多一个在途 deployment。节点下载后验证双签名、ZIP 与文件哈希，在目标级文件锁内 staging 并原子切换 current；重启时以 current symlink 为事实来源修复 state，无法证明状态时标记 degraded。

## Skill 后台同步与任务使用

1. Agent Activation/Reconcile 从 Release 的 Skill identity 集合维护每个 Runtime 的订阅数，并把各 Skill 的 `current_version_id` 写为 desired；current 变化推进 generation。
2. 平台 Worker 周期轮询 claim；节点由 WSS 通知后请求 desired。两者都按内容摘要查本地缓存，未命中才下载签名 Bundle。
3. Runtime 验证签名、Manifest 摘要、内容摘要与文件安全性，写入内容寻址缓存后回传 verified；控制面确认 generation 仍为当前目标，再通知 commit，Runtime 原子推进本地 applied 指针并回传 committed。
4. 同步失败按有限退避重试并保留旧 applied；首次没有 applied 的必需 Skill 会阻断 Activation/Task，不能静默缺省执行。
5. 任务领取后只核对本地 applied 缓存，为该 task 建立只读绑定并上报 Skill/version/digest/generation。执行结束移除任务绑定；整个热路径不访问控制面查询 Skill、不下载、不解包、不重新解析。
