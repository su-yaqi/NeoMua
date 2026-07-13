# conversation_management API

目录、会话、消息、委派、附件和上下文接口见根级 `context/apis.md`。创建、派生、消息和委派 mutation 使用幂等键；附件仅接受最大 5 MiB、通过 MIME/扩展名/UTF-8/控制字节检查的 TXT、Markdown 与 JSON，不向 API 暴露存储路径。会话固定 Runtime 与模型或精确 Agent Release，失效时阻断新执行。
