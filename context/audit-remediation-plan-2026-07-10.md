# NeoMua 2026-07-10 审计核查与修改计划

> 核查日期：2026-07-10<br>
> 核查对象：`context/audit-2026-07-10.md`、v0.4 PRD、runtime_management 文档及当前工作区代码<br>
> 本文性质：核查结论与修改计划，不包含代码修改

## 1. 结论摘要

原审计报告发现了多项真实问题，尤其是 Worker/Node 连接存活、事件事务、任务分发、会话串行化、任务超时、Gateway 作用域以及密钥继承问题。但报告也存在以下共性偏差：

1. 将“存在代码缺口”直接等同于报告描述的攻击或故障路径，未核对完整数据模型和已有幂等保护。
2. 部分建议与既定需求冲突。例如系统明确支持自建 Anthropic 兼容地址，不能直接全面禁止私网地址。
3. 部分建议不适用于既定多 Worker 架构。例如后端进程之间没有共享 WebSocket 表，无法可靠地由新连接直接关闭另一个进程持有的旧连接。
4. 多个编号描述的是同一根因，应合并修复，避免在不同位置叠加 try/except 形成不一致事务语义。
5. 个别条目不成立：H9 不会按报告所述造成事件丢失，M14 已由异步生成器形成自然背压，L12 已有持久化 dispatch 幂等保护，L4 与项目中文 UI 现状相反。

建议按 9 个独立变更集推进。P0 先解决系统存活、任务唯一执行、事件一致性和凭证边界；P1 再解决会话、超时、制品串行化、Gateway 健壮性与浏览器认证；P2 处理密码学迁移、部署加固和文档债务。每个变更集必须具备独立测试和回滚条件，不采用静默降级或绕过既有正式链路的方案。

## 2. 逐项核查结果

### 2.1 Critical

| 编号 | 结论 | 核查说明 | 处理方式 |
|---|---|---|---|
| C1 | 成立 | `RuntimeWorker.run_forever()` 不捕获 `run_once()` 异常；claim、事件上报和执行异常都可终止主循环。 | P0 修复；同时处理 L13，不能只在最外层吞异常。 |
| C2 | 成立 | `ConnectionClosed`/`WebSocketException` 和 Pydantic `ValidationError` 不在重连异常集合中，常规断线或协议坏包可退出 daemon。 | P0 修复；明确永久认证错误与可恢复会话错误。 |
| C3 | 部分成立，威胁边界已确认 | URL 未校验且会携带凭证请求属实。产品确认 namespace 管理员行为可信，因此不把管理员配置私网模型地址视为需要禁止的攻击面；但“修改 base_url、复用不可见旧密钥”可把密钥发送到新地址，必须修复。 | P0 允许私网模型；规范化后的 base_url 发生变化时，必须在同一请求重新提交密钥并重新完成兼容性验证。 |
| C4 | 部分成立 | 控制面在发送前没有原子预留属实；但任务绑定单一 `target_node_id`，不会被同 namespace 的任意两个节点领取，节点 spool 也会对相同 `(task_id, revision, snapshot)` 去重。真实风险是同设备身份的并发连接和发送/确认窗口。 | P0 引入短期 dispatch reservation，不直接在发送前置为不可恢复的 `DISPATCHED`。 |
| C5 | 成立 | 旧连接只在下一次入站消息时发现代次失效，期间仍可发送任务或制品。主动关闭旧 socket 不适用于多进程持有连接的架构。 | P0 在每次查询和每次发送前校验连接代次，并让 reservation 绑定 connection generation。 |

### 2.2 High

| 编号 | 结论 | 核查说明 | 处理方式 |
|---|---|---|---|
| H1 | 成立 | 事件“先查再插”存在 TOCTOU；唯一约束冲突会以未处理 `IntegrityError` 结束请求或 WebSocket handler。 | P0 与 H7 合并为原子事件写入事务。 |
| H2 | 成立 | `CANCELLING` 不允许接收 `RESULT -> SUCCEEDED`，晚到结果会被拒绝并可能让任务停留在 cancelling。 | P0 定义取消/完成竞争语义并用任务行锁排序。 |
| H3 | 成立（需求层面） | token 含 `task_id`，但 Gateway 请求路径和两端校验均无独立 task 上下文，违反“不能跨任务使用”的验收项。 | P0 使用 task-scoped Gateway URL/请求上下文，后端同时校验任务状态、runtime、model 和 token claim。 |
| H4 | 部分成立 | 官方 Compose 已把 `GATEWAY_SIGNING_KEY` 映射为 `SECRET_KEY`，所以不是当前标准部署的必现错误；非 Compose 部署仍会静默错配，当前 `/health` 也只返回常量。 | P1 增加跨服务签名探针和 readiness，降为部署健壮性问题。 |
| H5 | 成立 | session 消息创建无行锁，也不检查在途任务；并发轮次可使用同一个旧 `sdk_session_id`。 | P0 串行化同一 session，每次只允许一个非终态任务。 |
| H6 | 当前路径不构成故障 | 节点事件路径确实不写 `sdk_session_id`，但当前节点普通任务接口不能创建带 session 的多轮节点任务；平台多轮路径会持久化。报告将平台 PRD 的多轮要求直接套到未暴露的节点会话路径。 | P1 在重构统一事件事务时集中处理 session result，作为模型不变量；不单独按 High 修补。 |
| H7 | 与 H1 部分重复 | 相同序列、相同 payload 的普通重传会直接幂等返回，不会被标记 FAILED；并发插入会触发 H1 的 `IntegrityError`。真正不同 payload 的同序列冲突当前会标记失败，这是合理策略，但事务不安全。 | 合并到 P0 事件事务；保留“不同 payload 为协议冲突”。 |
| H8 | 成立，已核对 SDK 源码 | 锁定的 `claude-agent-sdk==0.2.110` 明确把 `os.environ` 与 `options.env` 合并，`INTERNAL_RUNTIME_TOKEN` 会进入 Claude CLI 子进程。 | P0 启动读取令牌后从进程环境删除，HTTP 客户端仅持有内存副本，并增加子进程环境测试。 |
| H9 | 报告所述后果不成立 | spool 的事件删除和重放是 per-task；全局 `last_acknowledged_event` 目前只出现在 reconcile payload，后端完全不消费，因此不会按报告所述漏删或漏传 task B 事件。 | P1 将问题改写为“对账摘要缺乏 per-task 信息且服务端未验证”，升级协议而非修复并不存在的丢事件路径。 |
| H10 | 成立 | lease 清理只由节点连接路径触发；无节点在线且没有其他触发时，过期任务不会被处理。 | P0 增加独立 maintenance loop/service，并以数据库锁保证多实例安全。 |
| H11 | 成立 | task create 的幂等检查存在并发窗口，commit 冲突未转回既有资源；task 与初始 user event 也不是一个事务。 | P0 使用数据库唯一约束作为仲裁，并把任务和初始事件放在一个事务中。 |

### 2.3 Medium

| 编号 | 结论 | 核查说明 | 处理方式 |
|---|---|---|---|
| M1 | 成立 | PostgreSQL READ COMMITTED 下，事件查询与终态查询可看到不同提交快照，存在最后事件遗漏窗口。 | P0 终态前做最终 drain，或用统一游标循环直到“终态且无新事件”。 |
| M2 | 成立 | 非整数 `Last-Event-ID` 直接 `ValueError`。 | P0 返回明确 400；不建议静默忽略无效游标。 |
| M3 | 部分成立 | 大小写/下划线变体不是 SDK 的合法 `PermissionMode`，报告未证明可映射到 bypass；但项目接受任意字符串并用 type ignore，违反显式能力校验。 | P1 建立受支持 allowlist，排除 `bypassPermissions`，删除 type ignore，并在 API/worker/node 三层共用校验。 |
| M4 | 成立 | 旧 credential 在 replacement 已签发但未 ack 时可继续认证，最长到原 90 天到期。简单 5 分钟强制吊销会在节点未持久化新 token 时破坏“离线恢复”。 | P1 设计可恢复的轮换宽限期、旧凭证重连恢复流程和定时吊销。 |
| M5 | 成立 | namespace 停用只影响列表，不影响 admin/runtime dependency，包括 superuser 的租户运行时操作。 | P1 为业务访问增加 active namespace guard，平台恢复/启用接口使用独立管理依赖。 |
| M6 | 原描述不成立，存在相邻问题 | rollback 入口只接受 `APPLIED` deployment，不会直接回滚一个 `DISPATCHED` deployment；但同 node/logical target 可同时存在新的 pending/dispatched 发布与旧部署的 rollback，顺序仍可能不确定。 | P1 对 `(node, logical_target)` 串行化部署；有在途部署时拒绝 rollback，除非以后新增显式 supersede/cancel。 |
| M7 | 部分成立 | `previous_artifact_id is None` 已检查，外键也保证一般情况下对象存在；但没有显式验证 namespace、logical target 和当前已安装指针。 | P1 补完整业务不变量校验。 |
| M8 | 成立 | 无安装锁，symlink 与 `state.json` 分两次原子替换，任一顺序都无法单靠重排消除中间 crash 不一致。 | P1 加每 logical target 进程锁，并让 recover 以 current symlink/版本目录校验和修复 state。 |
| M9 | 合理加固 | 指纹包含 API key 的无盐哈希，不会直接暴露高熵 key，但没有必要且会产生可关联标识。revision 已可表达配置变更。 | P2 指纹只包含非敏感路由字段或使用不可外发的本地 HMAC。 |
| M10 | 成立 | streaming 上游错误体原样进入客户端；恶意或异常上游可以回显请求信息。非流式路径同样直接返回上游 JSON。 | P1 对客户端返回稳定错误码，详细上游体仅进入受控、脱敏日志。 |
| M11 | 成立 | PyJWT 未要求 `exp` claim。当前签发器会写 exp，但验证端缺少防御性约束。 | P1 要求 `exp`、`iat`、`aud`、`runtime_id`、`task_id`、`model_id`。 |
| M12 | 合理加固 | 0 leeway 在时钟轻微漂移时会造成边界失败。 | P1 配置小幅 leeway，并监控节点/服务时钟。 |
| M13 | 成立 | 非流式 OpenAI 翻译直接索引 choices 并解析 arguments，异常供应商响应会变成 500。 | P1 返回结构化 502/unsupported response，不把上游坏数据当应用 bug。 |
| M14 | 不成立 | `_openai_stream` 是异步生成器；每次 yield 会把控制权交给 ASGI，下一次 `aiter_lines()` 读取受下游消费速度约束，不存在报告所述无界主动读取。 | 不修改；可补慢客户端集成测试和网关级响应大小/时长上限。 |
| M15 | 成立 | API 已支持 `AbortSignal`，该 Sheet 没有传入也没有卸载 cleanup。 | P1 加 `AbortController`、关闭/卸载 abort，并忽略 AbortError。 |
| M16 | 成立且范围更广 | 节点执行忽略已固化的 `timeout_seconds`；平台 worker 也没有执行超时，平台 session snapshot 甚至未完整固化该字段。 | P1 统一平台/节点超时语义，超时先 interrupt，再以明确终态和事件结束。 |
| M17 | 成立 | `_value` 对对象图无 cycle/depth 防护，`dataclasses.asdict` 本身也会递归；不能只在函数入口加一个简单深度参数。 | P0 使用带 visited 集合、深度和节点数限制的安全序列化器。 |

### 2.4 Low

| 编号 | 结论 | 核查说明 | 处理方式 |
|---|---|---|---|
| L1 | 部分成立 | 显示首尾字符是常见 mask，不等同于密钥泄露；但 direct runtime 用客户端 dict 第一个值决定 mask，可能展示错误字段。 | P2 按明确字段优先级选择 primary secret；是否改为全掩码由产品确认。 |
| L2 | 成立 | 当前是自实现 encrypt-then-MAC 流方案，虽有随机 nonce 和验签，但不应继续扩展自研密码协议。 | P2 引入版本化 AES-GCM/Fernet 格式，双读旧 v1，完成迁移后再停止写 v1。 |
| L3 | 成立 | `RuntimeAction` 只被自身单测引用，路由实际使用 dependency guards。 | P2 删除死代码和对应测试；权限事实保留在 dependency 层。 |
| L4 | 不成立 | 产品文档和 UI 主体均为中文，报告建议统一英文与项目方向相反。确有少量英文 API 错误，但这是完整 i18n 议题。 | 不纳入本次安全整改；另行定义语言规范后处理。 |
| L5 | 成立 | `INTERNAL_RUNTIME_TOKEN` 未检查；现有检查只匹配精确 `changethis`，也抓不到 `changethis-runtime-service-token`。 | P0 改为显式弱值集合/前缀检测，staging/production 缺失或弱值直接启动失败。 |
| L6 | 部分成立 | 官方 Compose 和部署 workflow 显式提供 ENVIRONMENT；直接启动应用时默认 local，且会挂载无认证 private route，仍有误部署风险。 | P2 用独立 `ENABLE_PRIVATE_TEST_API` 明确开启测试路由，非 local 禁止；不依靠默认环境值隐式开放。 |
| L7 | 成立但需先做秘密盘点 | `.env` 已被跟踪且 `.gitignore` 没有 `.env`；当前工作树可见值是模板占位值，不能据此断言真实生产密钥已泄露。 | P0 先盘点历史和部署来源；随后 untrack `.env`、增加 `.env.example`。只有发现真实凭证时才执行轮换和历史清理。历史重写须单独审批。 |
| L8 | 成立，已确认修复 | auth 文档明确规定 localStorage JWT，8 天 JWT 可被页面脚本读取；改为 httpOnly cookie 会改变 CSRF、跨域、OpenAPI 客户端和测试链路，不是局部修补。 | 纳入独立 P1-D 认证变更集；代码、测试与 context 文档必须同批更新。 |
| L9 | 成立但不是“无数据库认证” | Adminer 公开路由没有额外反向代理认证，但 Adminer 自身仍要求数据库登录。仍不应作为默认生产攻击面。 | P2 移到显式 debug profile/内网，不在生产默认 compose 暴露。 |
| L10 | 成立 | 握手 reason 区分 stale/signature/revoked，可减少为通用外部原因，详细原因写服务端安全日志。 | P2 修改。 |
| L11 | 合理加固 | 单次 1GB 有上限且仅 admin 可上传，但多个并发临时文件仍可耗尽节点磁盘。 | P2 增加可配置上限、并发额度和临时目录容量监控；仍须先落盘再验证 ZIP，不能假装可在未取得完整随机访问归档时完成全部校验。 |
| L12 | 不成立 | `record_dispatch` 已持久化 task_id/revision/snapshot；同 revision 返回 duplicate 不重启，高 revision 对同 task 返回 conflict。显式 retry 使用新 task ID。 | 不修改；补 restart + redispatch 回归测试即可。 |
| L13 | 成立但与 C1 重复 | 404/409 事件上报会抛异常。 | 合并到 C1 的任务级错误分类。 |
| L14 | 部分成立 | Playwright CI 会重新生成客户端，pre-commit CI 也会运行生成 hook 并处理差异；缺少只读 `git diff --exit-code` 的明确漂移门禁。`tenantApi` 是 architecture.md 明示的 Header 注入设计，不是偶然重复。 | P2 增加只读漂移 job；不在本次强制删除 tenantApi。 |
| L15 | 成立 | backend 的 `pydantic>2.0` 无上界，其他三个 workspace package 已使用 `<3`。 | P2 对齐为 `>=2.x,<3` 并更新 lock。 |

### 2.5 文档问题

| 编号 | 结论 | 处理方式 |
|---|---|---|
| D1 | 成立 | P2 更新 context 索引到 v0.4 和 runtime_management。 |
| D2 | 成立，口径已确认 | 统一为 PRD 规定的 10 分钟；修改代码默认值、测试和 flows 中的 15 分钟描述。 |
| D3 | 成立 | P2 为 runtime_management 表补字段、约束、索引和删除语义，不只写概述。 |
| D4 | 部分成立 | 节点断线恢复声明与 C2 冲突；应修代码而非删需求。平台多轮当前会持久化 `sdk_session_id`，报告用节点事件路径否定平台 PRD 不准确；若未来支持节点多轮，应明确另立需求。 |

## 3. 修改计划

### 3.1 P0-A：Worker 与 Node 存活和错误边界

**覆盖**：C1、C2、L13、M17。

**修改点**

1. 在 `runtime_worker/runtime_worker/worker.py` 把一次 claim/执行拆成明确阶段：
   - claim 无任务：正常短等待；
   - 网络、5xx：指数退避并记录结构化日志；
   - 401/403：配置致命错误，停止进程让编排器明确报警；
   - task 404/409：视为租约/任务所有权丢失，interrupt 当前 SDK，不杀主循环；
   - SDK 异常：转换为任务 ERROR 事件并可靠上报，不能只由外层 catch 吞掉。
2. lease task 不再通过 `gather(return_exceptions=True)` 静默丢失错误；用共享状态把取消、所有权丢失和临时续租失败传回主执行协程。
3. 给 `run_forever` 增加最后一道 supervisor，采用有上限且带 jitter 的 backoff；每次成功 claim/空轮询后重置 attempt。
4. 在 `node_runtime/node_runtime/connection.py` 捕获明确的 `WebSocketException`/`ConnectionClosed`、握手超时和协议 `ValidationError`；`PermanentConnectionError` 仍立即退出并提示重新注册。
5. 在 `runtime_worker/events.py` 用安全对象图序列化器替代无界递归：visited identity、最大深度、最大节点数、不可序列化值的受控字符串表示。不能使用会绕过 visited 的裸 `asdict()`。

**测试**

- runtime-worker：claim 5xx 后继续、事件 404/409 后 worker 存活、lease 失败中止当前任务、SDK 抛错生成 ERROR、退避重置。
- node-runtime：真实 `ConnectionClosed`、坏 JSON/Envelope、心跳 send 失败后重连；401/403 不重连。
- events：循环对象、深层 dataclass、超大对象、正常 SDK 消息不回归。

**验收**

- 可恢复故障不会退出 daemon；永久身份/配置错误不会被无限重试掩盖。
- 已领取任务的异常必须落明确终态或由租约维护器转为 interrupted。

### 3.2 P0-B：事件、状态机、幂等和 SSE 原子性

**覆盖**：H1、H2、H7、H11、M1、M2，并为 H6 提供统一入口。

**修改点**

1. 重构 `backend/app/runtime/repository.py`：移除 repository 内部零散 `commit()`，由调用方控制一个完整事务。
2. 新增统一的 `append_and_apply_event()`：
   - `SELECT agent_task ... FOR UPDATE`；
   - PostgreSQL `INSERT ... ON CONFLICT DO NOTHING RETURNING` 写事件；
   - 若冲突，读取既有事件并比较已脱敏后的 type/payload；相同则返回 duplicate 且不再次推进状态，不同则记录协议冲突并按策略终结任务；
   - 只有新插入事件才调用状态机；
   - RESULT 中存在 `session_id` 且 task 确有 session 时，在同一事务更新 `AgentSession.sdk_session_id`。
3. 取消和结果竞争采用“数据库提交顺序决定胜者”：
   - cancel 先锁定并提交：后到 RESULT 仍保留为审计事件，但业务状态转 `CANCELLED`，结果放入 `final_result.sdk_terminal`；
   - RESULT 先提交：cancel 看到终态后幂等返回 SUCCEEDED，不把已完成任务改成取消；
   - ERROR 在 CANCELLING 下仍可转 FAILED，保留真实失败原因。
4. `create_task`/retry 使用唯一约束仲裁并在 `IntegrityError` 后 rollback、重读、比较 request identity；task 和 sequence 0 USER_MESSAGE 同事务提交。
5. SSE 对非法 `Last-Event-ID` 返回 400。循环在观察到终态后再执行一次 `sequence > cursor` 的最终 drain，只有“终态且 drain 为空”才结束。

**数据库与兼容性**

- 不需要删除现有事件；保留 `(task_id, sequence)` 唯一约束。
- 若为冲突诊断增加字段，优先放入结构化 `final_result`，避免无必要迁移。

**测试**

- 使用两个独立 PostgreSQL Session/线程制造相同事件并发、不同 payload 冲突、相同 Idempotency-Key 并发。
- 覆盖 cancel-first、result-first、error-during-cancel 三种序列。
- SSE 用事务时序测试复现“事件查询后、状态查询前提交终态”。

**验收**

- 并发重复请求返回同一资源而非 500。
- 相同事件永远幂等，不同事件冲突可诊断且不会断开整个 node handler。
- SSE 必须包含最后一个持久化事件。

### 3.3 P0-C：节点分发、连接代次和租约维护

**覆盖**：C4、C5、H10，并同时保护制品下发入口。

**修改点**

1. 不采用“发送前直接把任务改成 DISPATCHED”的简单方案，因为进程在 send/accept 之间崩溃会把从未到达节点的任务变成 interrupted，违反 queued 任务可重新领取的需求。
2. 为 `agent_task` 增加短期分发预留字段，例如：
   - `dispatch_connection_id`；
   - `dispatch_reserved_until`。
3. 在独立同步 repository 函数中用 `FOR UPDATE SKIP LOCKED` 选取目标节点 QUEUED task，写入当前 connection generation 和短 reservation 后提交。发送失败时显式释放；进程崩溃时由 reservation 超时自动释放。
4. `task_accepted` 必须同时匹配 node、revision、当前 reservation connection；成功后才原子迁移为 DISPATCHED、写 claimed_by/lease 并清除 reservation。重复 accepted 只能由同一已确认 scope 幂等返回，不能覆盖 claimed_by。
5. `_send_pending_control`、`_send_runtime_config`、`_send_pending_artifacts` 在查询前及每次 send 前重新读取并比较 `RuntimeNode.connection_id`。旧连接发现失配后立即关闭。
6. 制品分发采用同样的 generation reservation。不能在释放行锁后仍保持 PENDING 再发送，避免两个 socket 同时下发同一 deployment。
7. 新建独立 maintenance loop/service，周期执行：
   - 释放过期 dispatch reservation；
   - `expire_task_leases`；
   - 过期 credential replacement 的后续处理；
   - 过期 artifact release/deployment。
   多实例通过 `FOR UPDATE SKIP LOCKED` 或 PostgreSQL advisory lock 保证可重复执行。

**迁移**

- Alembic 增加可空 reservation 字段和按 `target_node_id/status/reserved_until` 的查询索引。
- 迁移期间现有 QUEUED task 无 reservation，可正常领取；现有 DISPATCHED/RUNNING 不改状态。

**测试**

- 两个数据库 session 模拟同 node 双连接并发 claim，只有一个取得 reservation。
- send 前/后进程中止、reservation 过期、重复 task_accepted、旧连接 heartbeat。
- 无节点在线时 maintenance 仍把过期 RUNNING 任务转 interrupted。

**验收**

- 一个 task revision 最多启动一个 Agent Loop。
- 发送失败不丢 queued task，节点已收到但 ACK 丢失时依靠 spool 幂等重发。
- 旧连接不能再发送任务、配置或制品。

### 3.4 P0-D：Gateway、URL、任务作用域和敏感环境

**覆盖**：C3、H3、H8、L5，关联 M10/M11/M12。

**修改点**

1. 建立共享 endpoint 规范化：仅允许 `http/https`，拒绝 userinfo、fragment、异常端口和非规范 host；保留合法私网域名/IP，不增加私网 denylist/allowlist。
2. 比较修改前后的 canonical base URL。只要协议、host、port 或有效 path 发生变化，更新请求必须在同一请求体重新提交 secret；仅尾部 `/` 等规范化等价变化不视为地址变化。
3. 地址变化后清除旧的 compatibility verified 状态，使用新地址和新密钥重新验证；验证失败不得继续启用。任何情况下都不得把数据库里不可见的旧 secret 自动发送到新 base URL。
4. Gateway 改为 task-scoped 路径，例如 `/tasks/{task_id}/v1/messages`；平台和节点为每个 task 生成对应 base URL。Gateway 校验 URL task_id 与 token task_id 一致。
5. 控制面 `resolve_route` 接收并校验 task_id：task 存在、namespace/runtime/model 匹配、处于可执行非终态、token claim 一致。任务终态后 token 即使未到 exp 也不能再解析路由。
6. JWT 验证要求必要 claims，并配置小幅 leeway；具体放到 P1 Gateway 健壮性变更集实现。
7. `runtime_worker/main.py` 读取 `INTERNAL_RUNTIME_TOKEN` 后立即从 `os.environ` 删除；`RuntimeWorker.headers` 保存内存副本。测试启动 Claude CLI 时环境中不存在内部 token。
8. production/staging 配置校验拒绝缺失、`changethis*`、明显模板值或与其他关键密钥相同的内部 token。

**测试**

- 修改 base URL 不重新录入密钥必须 4xx；相同 origin 的 path 规范化按产品规则处理。
- token A 调 task B URL、终态 task token、错误 model/runtime/namespace 全部拒绝。
- 公网域名、RFC1918、IPv6 私网模型地址均可正常配置；非 HTTP scheme、userinfo 和非法 URL 被拒绝。
- base URL 实质变化但不提交新密钥时返回 4xx；同请求提交新密钥后必须重新验证，验证失败保持未启用。
- Claude CLI 子进程环境不含内部服务凭证。

**验收**

- 保存的 provider/runtime secret 不能通过只修改 URL 被引流。
- Gateway Token 满足 namespace/runtime/task/model 四维约束。
- 管理员可配置私网模型，不需要部署级 allowlist；旧密钥不能因地址修改被自动带到新目标。

### 3.5 P1-A：会话串行、统一超时和前端流生命周期

**覆盖**：H5、H6（不变量）、M15、M16、M3。

**修改点**

1. `create_session_message` 对 `AgentSession` 行加锁，检查同 session 是否存在 QUEUED/DISPATCHED/RUNNING/CANCELLING task；存在时返回 409 和当前 task id。
2. 把 runtime 配置中的 timeout、工具、cwd、permission mode 完整固化到平台 session task snapshot，避免 claim 时读取变化后的 runtime。
3. 定义共享 PermissionMode 枚举/校验函数；API、RunCommand 和 NodeTaskExecutor 都只接受确认支持且不含 bypass 的值。
4. 平台 worker 与 node executor 使用同一 timeout 语义：到时先 `AgentShell.interrupt(task_id)`，给短 grace，仍未结束再取消执行 task；写入结构化 timeout ERROR/STATUS 并释放 lease。
5. `TestConversationSheet` 在每次 send 建立 controller；关闭 Sheet、组件卸载或开始新 stream 时 abort 旧连接；AbortError 不显示为执行失败。
6. session result 持久化由 P0-B 的统一事件函数负责，未来若开放节点 session 不再需要复制逻辑。

**测试**

- 同 session 并发 POST 只有一个 task；前一任务终态后下一轮携带最新 sdk_session_id。
- 平台/节点 timeout、interrupt 成功、grace 后强制取消。
- React 组件关闭后 reader 被取消且不再 setState。

### 3.6 P1-B：凭证轮换与 Gateway 健壮性

**覆盖**：M4、H4、M10、M11、M12、M13。

**修改点**

1. credential 增加 replacement 生效/宽限信息。旧 credential 在宽限内只用于恢复轮换，不继续作为长期正常凭证。
2. 节点若持旧凭证重连且 replacement 未确认：服务端撤销无法取回明文的旧 replacement，重新签发一次新 credential；节点原子保存后重连确认。超过宽限才拒绝并要求重新配对。
3. maintenance 定时吊销超过宽限仍未确认的旧 credential，并记录审计原因。
4. Gateway 拆分 liveness/readiness。readiness 使用内部服务 token 从 backend 获取短时签名 probe，并用本地 key 验证，以检测 signing key、control URL 和 internal token 的跨服务错配。
5. Gateway JWT `decode` 要求 `exp/iat/aud/runtime_id/task_id/model_id/namespace_id`，设置配置化小 leeway。
6. 上游错误映射为稳定的 502/4xx 结构；响应体和 header 不透传到客户端。服务端日志只记录 request id、provider、状态码和脱敏摘要。
7. OpenAI 非流式翻译先验证 choices/message/tool call shape，JSON arguments 失败返回 `invalid_upstream_response`，不抛裸 IndexError/JSONDecodeError。

**测试**

- rotation 在“签发前、签发后未收到、节点保存后未 ack、宽限到期”各 crash point 恢复。
- Compose key 一致 readiness 成功，错配/内部 token 错误时 readiness 失败但 liveness 正常。
- 恶意上游回显 `Authorization`/`x-api-key` 时客户端响应和日志都不含 secret。

### 3.7 P1-C：制品发布串行和节点原子恢复

**覆盖**：M6、M7、M8。

**修改点**

1. 将 `logical_target` 固化到 deployment，或引入等价的 node-target lock 实体，使数据库能约束每个 `(node_id, logical_target)` 最多一个 PENDING/DISPATCHED deployment。
2. create release、retry、rollback 都在锁内检查在途部署。首版有在途任务时返回 409，不自动过期或覆盖；以后若需要 supersede，新增显式、可审计状态和 API。
3. rollback 必须验证：源 deployment 已 APPLIED；它仍是节点该 target 的 current；previous artifact 存在、同 namespace、同 logical target；目标版本曾在该节点成功安装。
4. installer 对每个 logical target 获取进程级文件锁，防止两个 apply 并行。
5. `recover()` 不只清 staging：读取 current symlink，验证目标版本目录，再与 state.json 对账。以已完成原子 symlink 切换的实际 current 为准，重建 current/previous 状态；无法证明 previous 时进入明确 degraded 状态并拒绝自动 rollback。
6. 对 symlink、state rename、fsync 的每个 crash point 编写故障注入测试。

**迁移与兼容**

- 为历史 deployment 回填 logical_target；无法关联 artifact 的异常行应先报告并停止迁移，不静默填默认值。
- 新约束上线前扫描重复 active deployment 并输出人工处理清单。

**验收**

- 同 node/target 不存在并发 apply。
- 任一 crash point 重启后 current 仍可验证，回滚不会指向错误版本。

### 3.8 P1-D：浏览器认证迁移到 HttpOnly Cookie

**覆盖**：L8，以及 auth/runtime_management 文档一致性。

**目标形态**

1. 浏览器不再把 access token 写入 `localStorage`，页面 JavaScript 无法读取长期登录凭证。
2. access token 使用短有效期 HttpOnly Cookie；refresh token 使用更长有效期、可轮换且可吊销的 HttpOnly Cookie。建议 access token 15 分钟，refresh session 保持现有 8 天登录体验。
3. Cookie 至少设置 `HttpOnly`、`Secure`（非 local）、`SameSite=Lax`、精确的 `Path`；默认使用 API host-only cookie，不把凭证扩大到整个父域。
4. 浏览器 cookie 鉴权与非浏览器 `Authorization: Bearer` 并存，避免破坏 CLI/API 集成；CSRF 防护只对 cookie 鉴权的状态修改请求生效。

**后端修改**

1. 登录接口为浏览器设置 access/refresh cookie；新增 refresh 和 logout 端点。logout 清理 Cookie 并吊销 refresh session。
2. 增加 refresh session 数据表，数据库只保存 refresh token 哈希、用户、过期时间、轮换链、吊销时间和必要的审计信息，不保存 refresh token 明文。
3. 每次 refresh 后吊销旧 token 并签发新 token；检测到已轮换 token 重放时吊销该 session 链。
4. 当前用户依赖同时接受 Bearer header 和 access cookie，并标识本次认证来源。
5. 对 cookie 鉴权的 POST/PUT/PATCH/DELETE 校验 CSRF token 与 `Origin`；Bearer header 请求不依赖 Cookie，因此不套用 CSRF 校验。
6. CORS 只允许配置的前端 origin 携带 credentials，不能使用通配 origin。

**前端修改**

1. 删除 `localStorage.access_token` 的写入、读取和清理逻辑；登录状态改由 `/users/me` 与 refresh 流程确定。
2. generated client、Axios `tenantApi`、原生 fetch/SSE 统一启用 credentials；状态修改请求统一发送 CSRF header。
3. 401 时只允许一次受控 refresh 和原请求重试；refresh 失败后清理客户端用户状态并跳转登录，不能形成无限重试或并发 refresh 风暴。
4. logout 调用后端端点，不再只是删除浏览器本地值。

**context 同步（与代码同一变更集完成）**

- `context/modules/auth/flows.md`：登录、刷新、退出、CSRF 和凭证存储流程。
- `context/modules/auth/api.md`：Cookie、refresh/logout 接口、Bearer 兼容方式和错误响应。
- `context/architecture.md`：浏览器 Cookie 会话与非浏览器 Bearer 双通道边界。
- `context/apis.md`：认证与 CSRF 全局规范。
- `context/ui.md`、`context/modules/auth/ui.md`：前端不再使用 localStorage token。
- `context/modules/runtime_management/api.md`：浏览器接口改述为会话 Cookie + namespace header，内部服务和节点认证保持不变。
- 如数据表落地，同步 `context/data-schema.md`。

**测试**

- 登录响应 Cookie 属性、access 到期自动 refresh、refresh rotation/replay、logout 吊销。
- JavaScript/localStorage 中不存在 access/refresh token。
- cookie 鉴权的跨站 mutation 被拒绝，合法 same-site + CSRF 请求通过。
- Bearer API 客户端继续工作；SSE、文件上传、namespace 切换和所有受保护页面回归。

**验收**

- 浏览器长期凭证不能被页面 JavaScript 读取。
- 保持现有登录体验，且 refresh token 可服务端吊销、不可重复使用。
- 代码、OpenAPI 客户端、E2E 与 context 文档在同一变更集内一致。

### 3.9 P2：密码学、部署与维护性加固

**覆盖**：M9、L1、L2、L3、L6、L7、L9、L10、L11、L14、L15、D1-D3。

**修改点**

1. secret ciphertext 引入 `v2` AEAD：随机 nonce、独立 purpose/version 作为 AAD；读兼容 v1/v2，新写只写 v2。
2. 提供显式迁移命令：事务性读取 v1、解密、写 v2、抽样验证；迁移完成前不得删除 v1 reader。密钥轮换另做 re-encrypt 流程。
3. primary mask 按已知凭证字段优先级选择，不依赖 dict 顺序；是否完全隐藏首尾字符待产品确认。
4. 删除未接入的 `RuntimeAction` 权限层，保留 dependency guards 为唯一事实来源。
5. private test API 改为显式 feature flag；production/staging 无论 flag 误设与否都拒绝启动或拒绝挂载。
6. `.env` 加入 `.gitignore`，提交仅含占位 schema 的 `.env.example`，从 index 移除 `.env`。先做历史秘密盘点：
   - 若只存在模板值，不做破坏性历史重写；
   - 若存在真实值，先轮换所有相关凭证和数据库密文，再经单独审批执行 `git filter-repo` 和团队重新同步。
7. Adminer 移出默认 production compose，放到显式 debug profile 或受认证内网入口。
8. WebSocket 对外统一 `authentication failed`，服务端安全日志保留分类但不记录 token/signature。
9. artifact 上传配置单文件/总大小/并发数，临时目录做容量预检和指标；达到阈值返回 429/507。
10. CI 增加“生成 OpenAPI 客户端后 `git diff --exit-code`”的只读 job；保留 tenantApi 的 namespace header/SSE 职责，后续再减少重复类型。
11. backend Pydantic 约束与其他 workspace 对齐 `<3`，更新 `uv.lock` 并跑全套类型/测试。
12. 更新 context 索引；将注册 token 默认 TTL 从代码和 flows 的 15 分钟统一为 10 分钟；补充 runtime_management 字段表和断线恢复说明。

## 4. 建议的变更集顺序

| 顺序 | 变更集 | 合并前置 | 主要风险 |
|---|---|---|---|
| 1 | P0-A Worker/Node 存活 | 无 | 错误分类错误可能掩盖永久配置故障。 |
| 2 | P0-B 事件与幂等事务 | 无 | 事务边界变化，需要真实 PostgreSQL 并发测试。 |
| 3 | P0-C 分发 reservation 与 maintenance | P0-B | 协议/数据库迁移，需兼容旧节点版本。 |
| 4 | P0-D Gateway scope/URL/env | P0-B | Gateway 路径变化，必须控制面、worker、node 同步发布。 |
| 5 | P1-A 会话/超时/UI | P0-A、P0-B | timeout 终态和取消语义必须一致。 |
| 6 | P1-B 轮换/Gateway 健壮性 | P0-D、maintenance | credential 数据迁移与 crash recovery。 |
| 7 | P1-C 制品串行/恢复 | P0-C | 节点文件系统 crash consistency。 |
| 8 | P1-D HttpOnly Cookie 认证 | 可独立于 runtime P1 开发 | 认证契约、CSRF、refresh rotation 与所有浏览器请求。 |
| 9 | P2 加固与文档 | 相应 P0/P1 | AEAD 和 git 历史操作需单独迁移/审批。 |

P0-C 和 P0-D 都改变 node/control/gateway 协议。需要增加 protocol capability/version 协商；旧节点不支持新 reservation/task-scoped Gateway URL 时应明确拒绝执行并提示升级，不能静默回退到旧协议。

## 5. 总体验证门槛

每个变更集至少执行与其范围相关的测试，P0/P1 全部完成后执行完整门禁：

```text
uv run --all-packages pytest backend/tests runtime_worker/tests model_gateway/tests node_runtime/tests
uv run ruff check backend runtime_worker model_gateway node_runtime
uv run mypy backend/app
uv run ty check backend/app
cd frontend && bun run lint && bun run build && bun run test
docker compose config
docker compose build
docker compose up -d --wait
```

此外必须新增三类当前测试缺口：

1. 多数据库连接并发测试，而不是在同一个 SQLModel Session 中顺序调用两次。
2. WebSocket 双连接/断线/重连和旧 connection generation 测试。
3. 节点文件系统与 credential 轮换的 crash-point 故障注入测试。

## 6. 需要确认的需求口径

已确认：

1. 节点注册 token 默认 TTL 统一为 10 分钟。
2. namespace 管理员行为可信，允许配置私网模型地址，不增加私网 denylist/allowlist；base URL 实质变化时必须在同一请求重新提交密钥并重新验证。
3. 浏览器 JWT 从 localStorage 迁移到 HttpOnly Cookie，按独立 P1-D 正式实施，完成时同步更新 context 文档。

以下事项仍会改变最终实现，进入对应变更集前需要确认：

1. **密钥掩码**：建议 UI 默认只显示“已配置/未配置”或固定 `****`；若运维确实需要辨识密钥版本，可保留末 4 位，但不再显示前 3 位。

## 7. 明确不采纳的原建议

- 不直接捕获所有 `Exception` 后无限重试；永久认证/配置错误必须显式失败。
- 不禁止管理员配置私网模型地址；只执行 URL 规范化、base URL 变化强制重新提交密钥和重新验证。
- 不依赖进程内 WebSocket 表主动关闭其他 FastAPI worker 持有的连接。
- 不把发送前直接置为 DISPATCHED 当成原子分发，因为会制造“未送达但不可自动领取”的任务。
- 不用简单调换 `state.json` 与 symlink 写入顺序冒充 crash consistency。
- 不为 M14 添加应用内无界/重复缓冲；现有生成器已有消费背压。
- 不因 L12 改写已有 spool dispatch 幂等模型。
- 不把中文消息统一成英文；语言规范需独立定义。
- 不在未确认真实秘密进入历史前执行破坏性的 git history rewrite。
