# NeoMua 固定开发联调流程

从现在开始，推荐统一使用根目录 `Makefile` 命令进行日常开发。

## 1) 启动与停止

```bash
make dev-up
make dev-down
```

## 2) 日常联调

```bash
make dev-ps
make dev-health
make dev-logs-backend
make dev-logs-frontend
```

前端模式切换:

```bash
make frontend-hot-up
make frontend-hot-logs
make frontend-static-up
```

## 3) 代码改动后的验证

```bash
make test-backend
make test-e2e
```

## 4) 后端 OpenAPI 变更后

```bash
make generate-client
```

## 5) 常用地址

- Frontend: <http://localhost:5173>
- Backend Docs: <http://localhost:8000/docs>
- Adminer: <http://localhost:8080>
- MailCatcher: <http://localhost:1080>
- Traefik UI: <http://localhost:8091>

## 6) 约定

- 后续我们优先使用上述 `make` 命令，不再分散记忆长命令。
- 如果某次迭代需要额外命令，会先补进 `Makefile` 再执行，保持流程统一。
