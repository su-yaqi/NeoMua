# auth 接口详情

> 全局规范见 `context/apis.md`。

## 接口列表
| Method | Path | 描述 |
|--------|------|------|
| POST | /login/access-token | 用户登录 |
| POST | /login/test-token | 校验当前 Token |
| POST | /password-recovery/{email} | 发起找回密码 |
| POST | /reset-password/ | 重置密码 |
| POST | /password-recovery-html-content/{email} | 预览找回密码邮件 HTML |

## 接口详情

### 登录
- **Method**：POST
- **Path**：`/api/v1/login/access-token`
- **描述**：使用 OAuth2 表单登录，返回 JWT。
- **权限**：公开

**请求体**

```text
username=<email>
password=<password>
```

**响应体**

```json
{
  "access_token": "jwt",
  "token_type": "bearer"
}
```

**可能返回的错误**

- `400 Incorrect email or password`
- `400 Inactive user`

### 校验 Token
- **Method**：POST
- **Path**：`/api/v1/login/test-token`
- **描述**：返回当前登录用户。
- **权限**：需要登录

### 找回密码
- **Method**：POST
- **Path**：`/api/v1/password-recovery/{email}`
- **描述**：对存在与不存在邮箱均返回统一提示，避免邮箱枚举。
- **权限**：公开

**响应体**

```json
{
  "message": "If that email is registered, we sent a password recovery link"
}
```

### 重置密码
- **Method**：POST
- **Path**：`/api/v1/reset-password/`
- **描述**：使用找回密码 token 更新密码。
- **权限**：公开

**请求体**

```json
{
  "token": "reset-token",
  "new_password": "new-password"
}
```

### 预览找回密码邮件
- **Method**：POST
- **Path**：`/api/v1/password-recovery-html-content/{email}`
- **描述**：超级管理员查看找回密码邮件 HTML 内容。
- **权限**：超级管理员
