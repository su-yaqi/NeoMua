# namespaces 接口详情

## 接口列表
| Method | Path | 描述 |
|--------|------|------|
| GET | /namespaces/mine | 当前用户可见空间列表 |
| GET | /namespaces/{namespace_id}/users | 读取空间成员 |
| POST | /namespaces/{namespace_id}/users | 新增空间成员 |
| PATCH | /namespaces/{namespace_id}/users/{user_id} | 更新空间成员 |
| DELETE | /namespaces/{namespace_id}/users/{user_id} | 移除空间成员 |
| GET | /platform/namespaces | 平台空间列表 |
| POST | /platform/namespaces | 创建空间 |
| PATCH | /platform/namespaces/{namespace_id} | 更新空间 |
| DELETE | /platform/namespaces/{namespace_id} | 删除空间 |

## 接口详情

### 我的空间
- **Method**：GET
- **Path**：`/api/v1/namespaces/mine`
- **描述**：返回当前用户可见空间；超级管理员返回所有空间。
- **权限**：需要登录

### 空间成员列表
- **Method**：GET
- **Path**：`/api/v1/namespaces/{namespace_id}/users`
- **描述**：读取指定空间的成员与其在该空间中的角色。
- **权限**：空间管理员或超级管理员

**命名空间定位**

- Path 参数 `namespace_id`
- 或请求 Header `X-Namespace-Id`

### 新增空间成员
- **Method**：POST
- **Path**：`/api/v1/namespaces/{namespace_id}/users`
- **描述**：创建新用户或把已有用户加入空间。
- **权限**：空间管理员或超级管理员

**请求体**

```json
{
  "email": "user@example.com",
  "full_name": "User",
  "password": "password123",
  "is_active": true,
  "role": "user"
}
```

### 平台空间管理
- **Method**：GET / POST / PATCH / DELETE
- **Path**：`/api/v1/platform/namespaces` 及其子路径
- **描述**：平台级空间 CRUD，支持在创建或更新时指定 `admin_user_id` 自动写入空间管理员关联。
- **权限**：超级管理员
