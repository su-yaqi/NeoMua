# users 接口详情

## 接口列表
| Method | Path | 描述 |
|--------|------|------|
| GET | /users | 超级管理员读取用户列表 |
| POST | /users | 超级管理员创建用户 |
| PATCH | /users/me | 更新当前用户资料 |
| PATCH | /users/me/password | 更新当前用户密码 |
| GET | /users/me | 读取当前用户 |
| DELETE | /users/me | 当前用户自助注销 |
| POST | /users/signup | 公开注册 |
| GET | /users/{user_id} | 读取指定用户 |
| PATCH | /users/{user_id} | 超级管理员更新用户 |
| DELETE | /users/{user_id} | 超级管理员删除用户 |
| GET | /platform/users | 超级管理员读取平台用户（含空间角色） |
| POST | /platform/users | 超级管理员创建平台用户并分配空间 |
| PATCH | /platform/users/{user_id} | 超级管理员更新平台用户 |
| DELETE | /platform/users/{user_id} | 超级管理员删除平台用户 |

## 接口详情

### 当前用户资料
- **Method**：GET / PATCH
- **Path**：`/api/v1/users/me`
- **描述**：读取或更新当前登录用户的邮箱、姓名。
- **权限**：需要登录

### 当前用户密码
- **Method**：PATCH
- **Path**：`/api/v1/users/me/password`
- **描述**：校验旧密码后更新新密码。
- **权限**：需要登录

### 当前用户注销
- **Method**：DELETE
- **Path**：`/api/v1/users/me`
- **描述**：删除自己的账号；超级管理员不能自删。
- **权限**：需要登录

### 平台用户列表
- **Method**：GET
- **Path**：`/api/v1/users`
- **描述**：模板原生的超级管理员用户列表接口，不附带空间角色。
- **权限**：超级管理员

**响应体**

```json
{
  "data": [
    {
      "id": "uuid",
      "email": "user@example.com",
      "is_active": true,
      "is_superuser": false,
      "full_name": "User",
      "created_at": "2026-06-09T00:00:00Z",
      "namespace_roles": []
    }
  ],
  "count": 1
}
```

### 平台增强用户列表
- **Method**：GET
- **Path**：`/api/v1/platform/users`
- **描述**：返回用户及其空间角色，是命名空间扩展后的平台用户接口。
- **权限**：超级管理员

### 平台增强用户创建
- **Method**：POST
- **Path**：`/api/v1/platform/users`
- **描述**：创建用户并写入 `namespace_assignments`。
- **权限**：超级管理员
