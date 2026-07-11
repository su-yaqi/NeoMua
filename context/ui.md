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
├── /system/namespaces
└── /system/llm-providers
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
| 用户管理 | /admin | 超级管理员 |
| User Settings | /settings | 登录用户 |

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
- 运行时页顶部固定展示平台运行时，下方为节点列表和签名内容区。admin 可配置、注册、吊销和发布；developer 可多轮测试和下发普通任务；user 不渲染页面内容或导航。
- 任务详情按事件类型展示，工具负载默认折叠；中断任务只提供显式“重新执行”，不显示自动重试。
- 一次性节点令牌只保存在对话框组件状态，关闭或刷新即消失，不写 localStorage。
