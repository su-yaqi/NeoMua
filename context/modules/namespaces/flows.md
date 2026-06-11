# namespaces 业务流程

## 核心流程

### 登录后选择空间

**步骤**
1. 登录后的布局组件调用 `/namespaces/mine`。
2. 若本地没有 `selected_namespace_id`，前端默认选择返回列表中的第一个空间。
3. 用户在顶部下拉框切换空间，前端把空间 ID 写入 `localStorage`。
4. `tenantApi` 在后续请求中自动带上 `X-Namespace-Id`。

### 平台创建空间

**步骤**
1. 超级管理员在空间管理页提交空间名称。
2. 前端创建请求时为 `code` 生成 `crypto.randomUUID()`。
3. 后端校验 `code` 唯一性并创建 `namespace` 记录。
4. 如传入 `admin_user_id`，则补写 `user_namespace_link`，角色设为 `admin`。

### 从空间列表进入空间成员管理

**步骤**
1. `superuser` 进入 `/system/namespaces`。
2. 在目标空间行点击“成员管理”按钮，路由跳转到 `/system/namespaces/{namespaceId}/members`。
3. 父级 `/system/namespaces` 路由通过 `Outlet` 渲染子页面。
4. 子页面读取当前空间成员列表，并允许进一步增删改成员。

### 空间管理员管理成员

**步骤**
1. 请求进入 `require_namespace_admin`。
2. 依赖项从 `X-Namespace-Id` 或 query/path 中解析当前空间。
3. 若当前用户是超级管理员则直接放行。
4. 否则检查 `user_namespace_link.role` 是否为 `admin`。
5. 通过后允许读取、创建、更新或移除空间成员。

### 空间成员新增的复用逻辑

**步骤**
1. 管理员在成员页填写邮箱、姓名、角色和必要时的初始密码。
2. 后端先按邮箱查找是否存在平台用户。
3. 若已存在，则复用原用户，仅补写当前空间的 `user_namespace_link`。
4. 若不存在，则先创建 `user` 记录，再创建空间成员关联。
5. 若该用户已属于当前空间，则拒绝重复添加。

## 业务规则

- `namespace.code` 全局唯一。
- 用户在同一空间内只能拥有一条成员关系。
- 空间角色只允许 `admin`、`developer`、`user`。
- 非 `superuser` 的空间管理员不能移除自己，也不能把自己从 `admin` 降级。
- 删除空间会先删除关联的 `user_namespace_link`，再删除空间本身。

## 数据读写

| 操作 | 表 | 说明 |
|------|-----|------|
| 读 | namespace | 空间列表与详情读取 |
| 写 | namespace | 创建、更新、删除空间 |
| 读 | user_namespace_link | 判定空间角色、读取空间成员 |
| 写 | user_namespace_link | 分配管理员、设置成员角色、移除成员 |
| 读/写 | user | 新增空间成员时可能创建用户 |

## 异常场景处理
| 场景 | 处理方式 |
|------|---------|
| 未提供空间上下文 | 返回 400 `namespace_id is required` |
| 非空间管理员访问成员接口 | 返回 403 |
| 空间不存在 | 返回 404 |
| 空间编码重复 | 返回 409 |
