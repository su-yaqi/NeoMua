# llm_configs 接口详情

## 接口列表
| Method | Path | 描述 |
|--------|------|------|
| GET | /llm/providers/catalog | 读取预置供应商目录 |
| GET | /llm/provider-configs | 读取当前空间接入配置列表 |
| POST | /llm/provider-configs | 创建接入配置 |
| PATCH | /llm/provider-configs/{config_id} | 更新接入配置 |
| POST | /llm/provider-configs/{config_id}/validate | 连接校验 |
| POST | /llm/provider-configs/{config_id}/sync-models | 同步模型列表 |

## 接口详情

### 供应商目录
- **Method**：GET
- **Path**：`/api/v1/llm/providers/catalog`
- **描述**：返回后端预置的供应商目录，包括展示名、鉴权方式、默认地址、是否支持连接校验、是否支持模型发现，以及该供应商需要的密钥字段和扩展字段定义。
- **权限**：空间管理员或超级管理员

### 接入配置列表
- **Method**：GET
- **Path**：`/api/v1/llm/provider-configs`
- **描述**：读取当前空间下的全部大模型接入配置及其模型清单；密钥仅返回掩码，不返回明文。
- **权限**：空间管理员或超级管理员

### 创建接入配置
- **Method**：POST
- **Path**：`/api/v1/llm/provider-configs`
- **描述**：为当前空间新增一条接入配置，并初始化模型集合。
- **权限**：空间管理员或超级管理员

**请求体**

```json
{
  "config_name": "DeepSeek 生产",
  "provider_slug": "deepseek",
  "base_url": "https://api.deepseek.com/v1",
  "enabled": true,
  "secret_inputs": {
    "api_token": "sk-xxx"
  },
  "extra_config": {},
  "manual_models": [
    {
      "model_id": "deepseek-v4-flash",
      "display_name": "DeepSeek V4 Flash"
    }
  ],
  "enabled_model_ids": ["deepseek-v4-flash"]
}
```

### 更新接入配置
- **Method**：PATCH
- **Path**：`/api/v1/llm/provider-configs/{config_id}`
- **描述**：更新配置名称、接入地址、启用状态、扩展参数、密钥和模型启用集合；供应商类型创建后不可更换。
- **权限**：空间管理员或超级管理员

### 连接校验
- **Method**：POST
- **Path**：`/api/v1/llm/provider-configs/{config_id}/validate`
- **描述**：若供应商支持探活，则使用已保存的密钥和地址执行在线校验，并更新 `validation_status`、`validation_message`、`last_validated_at`。
- **权限**：空间管理员或超级管理员

### 同步模型
- **Method**：POST
- **Path**：`/api/v1/llm/provider-configs/{config_id}/sync-models`
- **描述**：若供应商支持模型发现，则请求其模型目录并和前端提交的手工模型集合合并；不支持时返回 `unsupported` 状态并仅保留手工模型。
- **权限**：空间管理员或超级管理员

## 权限与上下文

- 以上接口统一依赖 `require_namespace_admin`。
- 当前空间通过 `X-Namespace-Id` Header 指定。
- 超级管理员可绕过普通空间成员角色限制。
