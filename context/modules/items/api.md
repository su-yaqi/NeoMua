# items 接口详情

## 接口列表
| Method | Path | 描述 |
|--------|------|------|
| GET | /items | 读取条目列表 |
| GET | /items/{id} | 读取单个条目 |
| POST | /items | 创建条目 |
| PUT | /items/{id} | 更新条目 |
| DELETE | /items/{id} | 删除条目 |

## 接口详情

### 读取条目列表
- **Method**：GET
- **Path**：`/api/v1/items`
- **描述**：分页读取条目；普通用户只能看自己的条目，超级管理员可看全量。
- **权限**：需要登录

**请求参数**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| skip | integer | 否 | 偏移量，默认 0 |
| limit | integer | 否 | 条数上限，默认 100 |

### 创建条目
- **Method**：POST
- **Path**：`/api/v1/items`
- **描述**：创建归属于当前用户的条目。
- **权限**：需要登录

**请求体**

```json
{
  "title": "Item title",
  "description": "Optional description"
}
```

### 更新条目
- **Method**：PUT
- **Path**：`/api/v1/items/{id}`
- **描述**：更新条目标题或描述。
- **权限**：需要登录；非超级管理员必须是 owner

### 删除条目
- **Method**：DELETE
- **Path**：`/api/v1/items/{id}`
- **描述**：删除条目。
- **权限**：需要登录；非超级管理员必须是 owner
