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
