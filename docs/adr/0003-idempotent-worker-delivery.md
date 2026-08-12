# ADR-0003：API 与 Worker 幂等

状态：Accepted（2026-08-12）

创建 API 要求 `Idempotency-Key`。相同键和相同规范化请求哈希返回同一 RouteBook/Workflow Run；相同键用于不同内容返回 `IDEMPOTENCY_CONFLICT`。

Celery task ID 与 Workflow Run ID 相同，并启用晚确认。业务版本以 `workflow_run_id` 唯一，任务重复投递时读取并复用已有版本，不重复产生正式事实。
