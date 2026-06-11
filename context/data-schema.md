# 数据模型

> 当前数据库实体集中定义在 `backend/app/models.py`，并通过 Alembic 管理迁移。

## 命名规范

- 表名：单数、小写下划线或模板既有命名（当前为 `user`、`item`、`namespace`、`user_namespace_link`）
- 字段名：小写下划线
- 主键：统一使用 UUID
- 外键：`<entity>_id` 形式

## 公共字段约定

当前业务表并未统一具备 `updated_at` 或软删除字段，已实现的公共字段如下：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| created_at | timestamptz | 创建时间；`User`、`Item`、`Namespace` 已实现 |

## 实体关系概览

```text
User 1 ---- N Item
User 1 ---- N UserNamespaceLink N ---- 1 Namespace
```

## 表结构

### user
系统用户表，保存登录账号、平台权限和基础资料。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| email | varchar(255) | 否 | - | 唯一邮箱，登录标识 |
| is_active | boolean | 否 | true | 是否启用 |
| is_superuser | boolean | 否 | false | 是否平台超级管理员 |
| full_name | varchar(255) | 是 | null | 用户姓名 |
| hashed_password | varchar | 否 | - | 加密后的密码 |
| created_at | timestamptz | 是 | now() | 创建时间 |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| ix_user_email | email | 唯一 | 登录与注册去重 |

### item
个人条目表，归属于单个用户。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| title | varchar(255) | 否 | - | 条目标题 |
| description | varchar(255) | 是 | null | 条目描述 |
| created_at | timestamptz | 是 | now() | 创建时间 |
| owner_id | uuid | 否 | - | 归属用户 |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| fk_item_owner_id | owner_id | 普通 | 用户条目查询与级联删除 |

### namespace
空间表，表示租户/工作空间层级的组织单元。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| name | varchar(255) | 否 | - | 空间名称 |
| code | varchar(64) | 否 | - | 空间编码，唯一 |
| is_active | boolean | 否 | true | 是否启用 |
| created_at | timestamptz | 是 | now() | 创建时间 |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| ix_namespace_name | name | 唯一 | 名称去重 |
| ix_namespace_code | code | 唯一 | 编码去重 |

### user_namespace_link
用户与空间的关联表，同时保存空间内角色。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|------|--------|------|
| id | uuid | 否 | uuid4 | 主键 |
| user_id | uuid | 否 | - | 关联用户 |
| namespace_id | uuid | 否 | - | 关联空间 |
| role | enum | 否 | user | 空间角色：`admin` / `developer` / `user` |

**索引**
| 索引名 | 字段 | 类型 | 说明 |
|--------|------|------|------|
| uq_user_namespace_link_user_namespace | user_id, namespace_id | 唯一 | 防止同一用户重复加入同一空间 |
