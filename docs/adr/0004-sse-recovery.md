# ADR-0004：SSE 非持久事件与恢复

状态：Accepted（2026-08-12）

Worker 先提交 PostgreSQL 状态，再通过 Redis Pub/Sub 发布 SSE 进度。事件仅用于实时体验，不作为业务事实，也不承诺历史重放。

断线客户端先读取 `GET /api/workflow-runs/{id}` 获取当前阶段，再订阅新事件。发布失败记录结构化错误，但不得回滚已提交版本。
