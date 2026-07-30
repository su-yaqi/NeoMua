# NeoMua 项目技术分析与代码规范

> 基于 `Full Stack FastAPI Template` 的当前代码库整理。本文档作为后续迭代的统一工程准则。

## 1. 技术栈总览

### 1.1 后端
- 语言与运行时: Python `3.10+`
- Web 框架: FastAPI
- 数据模型/ORM: SQLModel
- 配置管理与校验: Pydantic v2 + pydantic-settings
- 数据库: PostgreSQL（驱动 `psycopg`）
- 数据库迁移: Alembic
- 鉴权: OAuth2 Password Flow + JWT
- 密码安全: `pwdlib`（Argon2/Bcrypt）
- HTTP 客户端: httpx
- 邮件: emails + Jinja2 + MJML 模板（HTML 构建产物）
- 错误追踪: Sentry（非 local 环境启用）

### 1.2 前端
- 框架: React 19 + TypeScript
- 构建工具: Vite
- 路由: TanStack Router（文件路由）
- 服务端状态: TanStack Query
- 表单: react-hook-form + zod + @hookform/resolvers
- UI: Tailwind CSS v4 + Radix UI + shadcn/ui 组件体系
- 主题: next-themes
- API 客户端: `@hey-api/openapi-ts` 生成代码

### 1.3 工具链与工程化
- JS 包管理与任务运行: Bun（根 workspace + `frontend`）
- Python 依赖与虚拟环境: uv
- 前端格式化/Lint: Biome
- 后端格式化/Lint: Ruff
- 类型检查: mypy（strict）+ ty
- 测试:
  - 后端: pytest + coverage
  - 前端 E2E: Playwright
- 提交前门禁: pre-commit（自动格式化、静态检查、SDK 生成等）
- 容器与部署: Docker Compose + Traefik

## 2. 当前框架方法（项目实践）

### 2.1 后端分层
- `app/main.py`: 应用装配（Sentry、CORS、路由挂载）
- `app/api/main.py`: 路由聚合
- `app/api/routes/*`: 资源路由与 HTTP 协议层
- `app/api/deps.py`: 依赖注入（DB Session、CurrentUser、Superuser）
- `app/crud.py`: 数据访问与业务基础操作
- `app/models.py`: SQLModel 实体 + Pydantic 输入/输出模型
- `app/core/*`: 配置、数据库引擎、安全能力

方法要点:
- 路由层负责协议和鉴权边界，不直接散落复杂数据访问逻辑。
- CRUD 层集中数据写入/读取，维持可复用和可测试性。
- 通过依赖注入统一会话与身份上下文，避免在路由中硬编码。
- 使用 `response_model` 明确 API 契约，输入输出模型分离。

### 2.2 前端组织方式
- `src/routes`: 页面级路由与守卫（`beforeLoad`）
- `src/components`: 业务组件与通用组件分层
- `src/client`: 自动生成 API SDK（禁止手写改动）
- `src/hooks`: 复用逻辑（鉴权、交互、工具）
- `src/main.tsx`: 全局装配（QueryClient、Router、Theme、Toaster）

方法要点:
- 使用 TanStack Router 做路由与权限前置控制。
- 使用 TanStack Query 管理异步状态与错误统一处理（401/403 回登录）。
- 通过 OpenAPI 生成客户端，前后端契约同步演进。

### 2.3 开发与发布流程
- 本地开发默认 Docker Compose 起后端依赖栈；前端推荐本地 `bun run dev`。
- 后端模型变更先生成 Alembic revision，再执行 upgrade。
- 后端 OpenAPI 变化后必须重新生成前端 SDK 并提交。
- CI/本地都依赖统一脚本与 pre-commit 门禁。

## 3. NeoMua 代码规范准则（后续迭代强制执行）

## 3.1 通用原则
- 单一职责: 每个模块只承载单一层次职责。
- 显式契约: 输入/输出/异常都要可见、可推断。
- 先类型后功能: 新增代码必须具备类型标注。
- 可测试优先: 功能改动必须伴随测试或测试更新。
- 最小改动: 尽量局部修改，避免无关重构混入。

## 3.2 后端规范
- 路由层:
  - 仅处理参数解析、权限校验、响应模型映射。
  - 统一使用 `response_model`，状态码与错误信息保持一致语义。
- 数据与业务:
  - 数据访问优先放在 `crud.py` 或明确的 service/crud 模块。
  - 禁止在多个路由重复写同一段 SQLModel 查询逻辑。
- 模型与校验:
  - `Create/Update/Public` 模型分离，不复用数据库模型做所有场景。
  - 敏感字段（密码、token）禁止出现在 Public 模型。
- 安全:
  - 禁止提交硬编码密钥。
  - 所有需要登录的接口必须通过 `CurrentUser` 或 superuser 依赖守卫。
- 数据库变更:
  - 每次模型结构变更必须配套 Alembic migration 文件。

## 3.3 前端规范
- 路由与页面:
  - 页面入口放 `routes`，可复用 UI 放 `components`。
  - 受保护页面必须在路由层做登录拦截。
- 数据请求:
  - API 调用统一走 `src/client` 生成 SDK。
  - 禁止在业务代码中重复拼接 URL/手写 fetch 基础逻辑。
- 状态管理:
  - 服务端状态使用 TanStack Query。
  - 组件本地状态只存 UI 状态，不缓存服务端真值。
- UI 与样式:
  - 优先复用已有 `components/ui` 与现有设计 tokens。
  - 不破坏已有暗色模式与响应式行为。
  - 列表页交互一致性（强制）:
    - 涉及“编辑/删除”等行级操作时，必须对齐 `items` 列表模式：`DataTable` + `actions` 列 + `EllipsisVertical` 下拉菜单。
    - 下拉菜单项触发弹窗（编辑弹窗、删除确认弹窗），禁止在表格行内直接摆放编辑/删除按钮。
    - 新增同类页面时默认复用该模式，除非有明确的产品设计例外并在 PR 说明中写明原因。

## 3.4 质量门禁规范
- 提交前必须通过:
  - `pre-commit run --all-files`
  - 后端测试: `bash ./scripts/test.sh`
  - 前端 E2E（涉及交互变更时）: `bunx playwright test`
- 若后端 OpenAPI 有变更，必须执行并提交:
  - `bash ./scripts/generate-client.sh`
- 任何跳过测试/检查的情况，PR 描述必须写明原因与风险。

## 3.5 Git 与协作规范
- 分支命名建议: `feature/*`、`fix/*`、`refactor/*`。
- Commit 建议遵循 Conventional Commits（如 `feat:` `fix:` `refactor:` `test:` `docs:`）。
- 一个 commit 尽量只做一件事，便于回滚和评审。
- 禁止提交:
  - `.env` 中真实密钥
  - 本地构建产物与临时调试文件

## 3.6 文档与可维护性规范
- 新增模块时，必须补充最小必要文档（用途、输入输出、边界条件）。
- 非显而易见逻辑需添加简洁注释，解释“为什么”，不是“做了什么”。
- 对外 API 行为变化，必须更新对应 README 或开发文档片段。

## 4. 迭代执行约定（对未来改动生效）

从本次开始，NeoMua 的后续改动默认遵循本规范：
- 我在后续实现中会以本文档为默认约束进行设计、编码、测试与评审。
- 若你希望偏离某条规则（例如快速 PoC 跳过部分测试），我会先明确标注偏离项、风险与补偿措施，再执行。
