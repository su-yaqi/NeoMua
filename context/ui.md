# 前端总览

> 前端位于 `frontend/`，采用 TanStack Router 文件路由。各模块页面细节见 `modules/*/ui.md`。

## 设计规范

- 组件库：shadcn/ui + Radix UI
- 样式系统：Tailwind CSS 4
- 布局：认证页使用独立 `AuthLayout`；登录后使用左侧 Sidebar + 顶部栏 + 内容区
- 主题：支持浅色 / 深色 / 跟随系统
- 响应式：Sidebar 与用户菜单具备移动端收起逻辑，整体以后台桌面场景为主

## 页面树

```text
/login
/signup
/recover-password
/reset-password?token=...

/
├── /items
├── /admin
├── /settings
├── /workspace
│   └── /conversations/:conversationId
├── /workflows
├── /projects
│   └── /:projectId
├── /apps/:workflowSlug
│   ├── /new
│   └── /tasks/:instanceId
├── /system/namespaces
├── /system/llm-providers
├── /system/agents
│   ├── /:agentId
│   └── /:agentId/releases/:releaseId
├── /system/skills/:skillId
├── /system/tools
├── /system/mcp-servers/:mcpServerId
├── /system/plugins/:pluginId
├── /system/agent-activations/:activationId
├── /system/workflows
│   └── /:templateId/versions/:versionId/configuration
├── /system/spec-standards
└── /system/runtimes
    ├── /nodes/:nodeId
    ├── /tasks/:taskId
    ├── /artifacts
    └── /releases/:releaseId
```

## 导航结构
| 导航项 | 路径 | 权限 |
|--------|------|------|
| Dashboard | / | 登录用户 |
| Items | /items | 登录用户 |
| 空间管理 | /system/namespaces | 当前实现中所有登录用户都可看到，实际数据接口由后端控制 |
| 大模型接入配置 | /system/llm-providers | 当前实现中已登录且已选空间用户可看到，实际数据接口由后端空间管理员权限控制 |
| 运行时管理 | /system/runtimes | namespace admin/developer；user 不显示 |
| Agent 管理 | /system/agents | namespace admin 可编辑/发布，developer 只读，user 不显示 |
| Skills / Tools / MCP Servers / Plugins | `/system/*` | admin 管理，developer 读取脱敏状态 |
| 用户管理 | /admin | 超级管理员 |
| User Settings | /settings | 登录用户 |
| AI 工作台 | /workspace | 所有 namespace 成员；Chat/Agent 双栏会话 |
| 项目 | /projects | 项目成员读取；Admin/Developer 配置 |
| Workflow | /workflows | 所有 namespace 成员；已启用流程应用卡片目录 |
| Workflow 应用 | /apps/:workflowSlug | 应用切换、实例列表及应用专属创建/详情 |
| Workflow / Spec 管理 | /system/workflows、/system/spec-standards | 成员读；Admin/Developer 发布配置 |

## 公共组件
| 组件名 | 用途 |
|--------|------|
| `AuthLayout` | 登录、注册、找回密码、重置密码页共用布局 |
| `AppSidebar` | 登录后主导航 |
| `DataTable` | 列表页通用表格 |
| `Appearance` | 主题切换 |
| `Footer` | 登录后全局页脚 |
| `ErrorComponent` / `NotFound` | 路由异常与 404 展示 |

## 路由与权限约定

- `/_layout` 在 `beforeLoad` 中调用 `/users/me` 验证 HttpOnly Cookie 会话；不读取本地 Token。
- `/login`、`/signup`、`/recover-password`、`/reset-password` 对已登录用户做重定向。
- `/admin` 在进入页面前再次验证 `is_superuser`。
- 顶部空间选择器将当前空间写入 `localStorage.selected_namespace_id`，并通过 `tenantApi` 自动附加到请求头。
- 布局层会在 `/namespaces/mine` 返回后再校验并修正本地缓存的空间 ID，避免首次渲染时因空间列表尚未加载完成而误清空当前选择。
- 运行时页按平台内置、服务节点、客户端和历史来源分组：平台内置 Runtime 显示“无需配置”及自动模型路由；Admin 可分别生成 service/client 一次性 bootstrap，在目标主机执行包含平台签名公钥但不含 enrollment 密文的命令，并通过隐藏输入提交一次性凭证。节点详情展示 bootstrap/receipt/Registry/发现代次与脱敏观察，client 支持受控重扫，service/client 支持审计式暂停/恢复；实例配置与模型路由只读。Developer 只读，User 不渲染页面与导航。
- 大模型配置详情同时展示连接探活、逐模型最小真实调用、平台协调任务、Worker 发行信任和最终 Binding；任一环节失败保持 blocked，不用单一“连接成功”掩盖 Runtime 尚不可执行。
- 任务详情按事件类型展示，工具负载默认折叠；中断任务只提供显式“重新执行”，不显示自动重试。
- 一次性节点令牌只保存在对话框组件状态，关闭或刷新即消失，不写 localStorage。
- Agent 能力页选择稳定模型偏好和引擎中立策略；对 Skill 只选择身份并启用/停用，对 Plugin 仍选择精确 Version，对 MCP 选择已发现 Tool。Release 页必须选择明确 Runtime Instance 并通过服务端 precheck 后才能创建 Activation；Harness 历史页只读。界面不提供 Skill Version 或供应商路由锁定控件。
- Activation 详情逐目标展示 attempt、digest 和错误；失败目标由 Admin 显式 Retry，已应用目标通过确认对话框创建可审计 Rollback，不自动替用户决策。
- `/system/skills` 是外层资产列表，展示名称、唯一标识、当前发布版本、草稿状态和 Runtime 同步摘要；进入 `/system/skills/:skillId` 后使用左侧文件目录树、右侧编辑/预览的工作台。Markdown 支持编辑与渲染预览，其他文本可编辑，二进制文件只显示元数据；版本历史和 Runtime desired/applied 状态在同一工作台中可追踪。
- Skill/Plugin/MCP 详情父路由显式渲染子路由出口，列表页不会遮蔽详情页。Skill 发布前必须完成草稿校验；已发布版本只读，current version 切换和失败同步重试均为显式操作。
- 工作台仅保留 Chat 和 Agent，采用左侧历史会话、右侧历史消息与底部输入区的常见聊天布局。项目和 Runtime Instance 创建后只读；Chat 选择 exact model binding，Agent 选择 exact 或严格的偏好解析模式，参与者可增减并指定一个组织 Agent，变更保存为后续消息使用的配置修订。
- 项目详情按成员、仓库、Spec 与任务分区；Spec 位置可绑定精确不可变标准版本。归档必须二次确认，归档后表单只读。
- `/workflows` 只展示已启用且前端组件已随构建发布的应用卡片；应用页左上可切换应用，主体是实例列表，右上新建。新建实例只填写任务名称和“要做什么”，项目、Runtime 和 Agent 在模板配置页完成；应用专属详情继续显示精确模板版本、流程图、节点页签及服务端冲突/阻断。
- 项目、Agent、Skill、MCP、Plugin 与 Spec 标准均从列表标题区按钮打开完整创建弹窗/页面；一次提交完成必要配置，不再先创建只有名称和 Slug 的空壳。所有用户可见的 `slug` 标签统一显示为“唯一标识”。
