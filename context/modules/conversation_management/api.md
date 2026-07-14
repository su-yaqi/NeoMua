# conversation_management API

目录、会话、消息、委派、附件和上下文接口见根级 `context/apis.md`。创建、派生、消息和委派 mutation 使用幂等键；附件仅接受最大 5 MiB、通过 MIME/扩展名/UTF-8/控制字节检查的 TXT、Markdown 与 JSON，不向 API 暴露存储路径。

会话创建后固定项目与 Runtime。`POST /conversations/{id}/configuration-revisions` 使用 `expected_revision` 追加配置修订：Chat 可更换 provider/model；Agent 可增减参与 Release 并指定其中唯一一个组织 Agent。存在在途轮次、目标未激活或 CAS 冲突时返回明确错误，不隐式应用部分配置。每条消息保存实际使用的 `configuration_revision_id`。
