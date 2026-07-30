# v0.4 PRD

## 版本说明
- 版本：v0.4
- 状态：已完成
- 创建时间：2026-07-01

## 本版本目标
在 namespace 隔离边界内提供基于 Claude Agent SDK 的平台与节点运行时，支持模型路由、多轮测试、节点配对与长连接、远程 Agent 任务和受控内容分发。

## 实施阶段

1. 平台运行时与 Model Gateway。
2. 节点安装、配对、长连接与心跳。
3. 节点 Agent 任务执行与完整过程回传。
4. 签名制品的受控分发、应用与回滚。

每个阶段必须独立通过验收后再进入下一阶段，不以轮询、任意远程命令或静默降级替代设计中的正式链路。

## 验收证据

- 后端 PostgreSQL 测试：132 项通过。
- `runtime_worker`、`model_gateway`、`node_runtime`：37 项通过。
- 前端 Playwright 完整回归：61 项通过（包含 namespace 角色、运行时列表、任务详情及内容重试/回滚权限）。
- Ruff 检查及格式、Biome、TypeScript、Vite 生产构建全部通过。
- Alembic 在独立空库完成全链升级，并完成 `8d826e5f3a54` 降级/再升级回环。
- Compose 从空卷完成迁移和启动；backend、model-gateway、runtime-worker 健康，节点运行时独立镜像构建成功。
- npm 与 Bun 锁文件同步，`npm audit --audit-level=low` 为 0 漏洞。

## 文件组织
| 文件 | 说明 |
|------|------|
| namespace-platform-runtime.md | 空间级平台运行时、模型路由、Gateway 与多轮功能测试 |
| namespace-node-runtime-enrollment.md | 节点运行时安装、配对、设备凭证、长连接与离线恢复 |
| namespace-agent-task-execution.md | 平台与节点 Agent 任务、多轮会话和完整执行事件 |
| namespace-runtime-content-delivery.md | Agent、Skill、MCP、CLI 配置等签名制品的受控分发 |
