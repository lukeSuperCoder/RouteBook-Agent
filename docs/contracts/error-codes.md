# 一期错误码合同

所有 API 错误统一返回：

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "路书基础版本已变化，请基于最新版本重试。",
    "request_id": "request-id",
    "details": {}
  }
}
```

| HTTP | code | 使用场景 |
| --- | --- | --- |
| 422 | `VALIDATION_ERROR` | Body、Path、Header 或领域输入无效 |
| 404 | `NOT_FOUND` | 路书、版本、Workflow Run 或提案不存在 |
| 409 | `IDEMPOTENCY_CONFLICT` | 同一幂等键对应不同请求内容 |
| 409 | `VERSION_CONFLICT` | 提交时基础版本已不是当前版本 |
| 503 | `DEPENDENCY_UNAVAILABLE` | PostgreSQL、Redis 或任务代理不可用 |
| 500 | `INTERNAL_ERROR` | 未分类内部错误；响应不暴露异常详情 |
