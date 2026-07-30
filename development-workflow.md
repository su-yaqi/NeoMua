# NeoMua 本地开发与联调流程

变更分级、分支、PR、context impact 与完成标准见 [development-process.md](./development-process.md)。

本地开发统一通过根目录 `Makefile` 进入。不要直接运行 `docker compose up/down`；统一入口负责固定实例身份、端口计算、项目归属检查和冲突报告。

## 1. 初始化并启动

每个 worktree 选择一个明确的 `DEV_SLOT`，同一 worktree 后续继续使用该编号：

```bash
make DEV_SLOT=0 dev-init
make DEV_SLOT=0 dev-preflight
make DEV_SLOT=0 dev-up
```

`dev-init` 只会在 `.env` 不存在时从 `.env.example` 创建本地文件，不覆盖已有配置。示例密钥只允许用于本地开发。

启动成功后命令会显示该实例的稳定访问地址。也可以随时查看：

```bash
make DEV_SLOT=0 dev-urls
make DEV_SLOT=0 dev-ps
make DEV_SLOT=0 dev-health
```

## 2. 实例身份和端口

默认项目范围为 `neomua`，Compose project name 固定为：

```text
neomua-dev-<DEV_SLOT>
```

端口块按以下公式确定，不随机寻找空闲端口：

```text
PORT_BASE = 20000 + DEV_SLOT * 100
```

| 服务 | 偏移 | `DEV_SLOT=0` | `DEV_SLOT=1` | 默认启动 |
|---|---:|---:|---:|---|
| Frontend | +0 | 20000 | 20100 | 是 |
| Backend | +1 | 20001 | 20101 | 是 |
| Model Gateway | +2 | 20002 | 20102 | 是 |
| 本地 Proxy HTTP | +3 | 20003 | 20103 | 否 |
| Traefik Dashboard | +4 | 20004 | 20104 | 否 |
| Adminer | +5 | 20005 | 20105 | 否 |
| Mailcatcher UI | +6 | 20006 | 20106 | 否 |
| Mailcatcher SMTP | +7 | 20007 | 20107 | 否 |

PostgreSQL 不暴露宿主机端口。需要数据库管理时使用 Adminer 或：

```bash
make DEV_SLOT=0 dev-db-shell
```

如需在另一项目范围复用脚本，可显式设置 `DEV_PROJECT_SCOPE`。范围只能包含小写字母、数字和连字符：

```bash
make DEV_PROJECT_SCOPE=neomua-review DEV_SLOT=0 dev-up
```

项目范围只改变资源身份；NeoMua 的宿主机端口仍由 `DEV_SLOT` 唯一决定，因此并行实例必须使用不同 slot。

## 3. 冲突和归属保护

每次可能启动服务前，统一入口会检查：

- `DEV_SLOT` 是否在 0–99；
- 同名 Compose project 是否属于当前 worktree；
- 即将使用的宿主机端口是否被其他容器或本机进程监听。

冲突时命令报告容器名、Compose project、工作目录或本机进程，并直接失败。它不会改用随机端口，也不会停止、删除或重建占用者。

`dev-down`、前端模式切换等命令同样先核对项目归属，只会操作当前 `DEV_PROJECT_SCOPE + DEV_SLOT` 对应的项目。

## 4. 默认服务与按需工具

`dev-up` 默认启动应用核心：

- PostgreSQL、prestart、Backend；
- Frontend；
- Model Gateway、Runtime Worker。

默认宿主机只暴露 Frontend、Backend 和 Model Gateway。辅助工具按需启动：

```bash
make DEV_SLOT=0 dev-tools-up
make DEV_SLOT=0 dev-tools-down
```

`dev-tools-up` 启动 Adminer 和 Mailcatcher。需要本地域名路由或查看 Traefik 时再启动：

```bash
make DEV_SLOT=0 dev-proxy-up
make DEV_SLOT=0 dev-proxy-down
```

## 5. 日常联调与热更新

```bash
make DEV_SLOT=0 dev-logs
make DEV_SLOT=0 dev-logs-backend
make DEV_SLOT=0 dev-logs-frontend
make DEV_SLOT=0 dev-watch
```

静态前端与 Vite 热更新前端共用该 slot 的 Frontend 端口，统一入口只切换当前项目内的前端服务：

```bash
make DEV_SLOT=0 frontend-hot-up
make DEV_SLOT=0 frontend-hot-logs
make DEV_SLOT=0 frontend-static-up
```

## 6. 验证

```bash
make DEV_SLOT=0 dev-config
make DEV_SLOT=0 test-backend
make DEV_SLOT=0 test-e2e
```

两类测试使用派生项目 `neomua-dev-<DEV_SLOT>-test` 和一次性数据卷，宿主机不发布测试端口。测试开始前只清理同一 worktree 拥有的派生测试项目，结束时恢复为不存在；不会复用或清理联调数据库，也不会与运行中的 Runtime Worker 竞争任务。

后端 OpenAPI 变化后运行：

```bash
make generate-client
```

## 7. 停止

```bash
make DEV_SLOT=0 dev-down
```

该命令只移除当前隔离项目的容器和网络，不删除数据卷，也不操作其他 Compose project。

## 8. 生产边界

本地入口显式组合 `compose.yml` 与 `compose.override.yml`，并注入开发实例名、宿主机端口和本地 URL。生产与 staging 仍只使用 `compose.yml`，不读取 `DEV_SLOT`，容器内部端口和生产网络语义不变。
