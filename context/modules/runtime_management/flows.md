# Runtime Management Flows

## 三类 Runtime 与节点注册

1. namespace 初始化或读取目录时幂等创建平台内置 Claude Agent SDK；平台 Worker 应用系统修订并上报能力后即可承接已验证模型。
2. Admin 登录目标 Linux/systemd 节点执行 NeoMua 命令：service 安装管理端与受管 SDK；client 只安装管理端，并发现已有 Claude Code/Codex。
3. 安装器先生成 Ed25519 密钥，提交模式与主机事实，验证平台签名的精确发行清单，再以设备证明完成 enrollment。
4. 身份和配置先写入同目录 staging，再原子切换；systemd 激活失败时保留完整状态，重复同命令只恢复服务安装，不重新注册。
5. enrollment 响应丢失时才发起 key-bound recovery；普通 409、模式冲突、验签失败或不支持的主机直接阻断。
6. 节点建立出站 WSS 并上报发现；service/client 报告违反类型边界时控制面拒绝，不创建兼容替代 Runtime。
7. client 管理端验证整份签名 Registry，只检查每个 Adapter 的有限绝对候选；探测使用清洗环境、结构化 argv、超时和输出上限。同一引擎多个安装分别分配本地 UUID，控制面永不接收绝对路径或登录凭证。
8. 外部引擎、文件指纹、Adapter 或能力变化会先清除旧 applied/Binding 证据并进入 `change_detected/validating`；安装消失保留 Runtime ID。任务前再次复核同一路径、指纹、身份、版本和登录，不得切换到 PATH 中其他程序。

## 任务执行

任务创建时选择 Runtime Instance 和具体 Runtime Model Binding，冻结模型路由、模型、工具、目录 allowlist、权限模式、超时、配置摘要、能力指纹和执行器版本。同一 session 同时最多一个在途任务。控制面先持久化 Task 与模型准备记录，再按 connection generation 建立短期 reservation；Runtime 在首次模型调用前从本地 applied 配置、实际 Adapter、能力缓存和已验证模型路由构造证据。证据与冻结快照不一致时拒绝任务，不签发 Model Gateway 凭据。每次终端模型执行按独立调用序号保存 binding、事件序号、usage、状态与脱敏错误。

平台 Runtime 与 service Runtime 的 Provider 路由都来源于同一模型级真实调用证据，但分别绑定各自当前能力指纹；client Runtime 只使用本地 native 登录和模型路由，不回退到平台 Provider Config。没有可用 Binding 的 Runtime 不进入任务目录。

事件在任务行锁内原子写入并推进状态，相同序列/载荷幂等，不同载荷形成可诊断协议冲突。平台与节点超时都先 interrupt，grace 后取消并写入 `task_timeout`。租约过期只标记 interrupted，必须显式 retry。

## 内容分发

admin 上传 ZIP 前先取得数据库并发额度并检查临时目录容量；平台逐文件计算哈希、拒绝路径逃逸/符号链接并签署不可变清单。每个 `(node, logical_target)` 同时最多一个在途 deployment。节点下载后验证双签名、ZIP 与文件哈希，在目标级文件锁内 staging 并原子切换 current；重启时以 current symlink 为事实来源修复 state，无法证明状态时标记 degraded。

## Skill 后台同步与任务使用

1. Agent Activation/Reconcile 从 Release 的 Skill identity 集合维护每个 Runtime 的订阅数，并把各 Skill 的 `current_version_id` 写为 desired；current 变化推进 generation。
2. 平台 Worker 周期轮询 claim；节点由 WSS 通知后请求 desired。两者都按内容摘要查本地缓存，未命中才下载签名 Bundle。
3. Runtime 验证签名、Manifest 摘要、内容摘要与文件安全性，写入内容寻址缓存后回传 verified；控制面确认 generation 仍为当前目标，再通知 commit，Runtime 原子推进本地 applied 指针并回传 committed。
4. 同步失败按有限退避重试并保留旧 applied；首次没有 applied 的必需 Skill 会阻断 Activation/Task，不能静默缺省执行。
5. 任务领取后只核对本地 applied 缓存，为该 task 建立只读绑定并上报 Skill/version/digest/generation。执行结束移除任务绑定；整个热路径不访问控制面查询 Skill、不下载、不解包、不重新解析。
