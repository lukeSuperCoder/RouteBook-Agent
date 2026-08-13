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
| 404 | `PLACE_NOT_FOUND` | 供应商没有返回符合条件的地点 |
| 409 | `PLACE_AMBIGUOUS` | 存在多个合理地点候选，需要用户确认 |
| 404 | `ROUTE_NOT_FOUND` | 供应商没有返回可用路线 |
| 502 | `PROVIDER_ERROR` | 未分类供应商业务错误 |
| 502 | `PROVIDER_AUTH_FAILED` | 供应商鉴权或权限配置失败；不重试 |
| 502 | `PROVIDER_BAD_RESPONSE` | 供应商响应无法解析或不符合合同 |
| 404 | `PROVIDER_DATA_UNAVAILABLE` | 供应商不支持该位置或暂无请求范围内的数据 |
| 503 | `PROVIDER_UNAVAILABLE` | 供应商超时、网络失败或服务端故障 |
| 503 | `PROVIDER_RATE_LIMITED` | 供应商 QPS、日配额或账户额度受限 |

供应商错误的 `details` 只允许包含 `provider`、`operation`、脱敏状态码和 HTTP
状态，不得包含 Key、认证 Header、完整请求 URL、精确地址或原始响应。
