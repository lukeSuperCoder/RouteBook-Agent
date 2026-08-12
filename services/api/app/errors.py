from __future__ import annotations

from typing import Any


class AppError(Exception):
    code = "INTERNAL_ERROR"
    status_code = 500
    public_message = "服务暂时不可用，请稍后重试。"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.public_message)
        self.message = message or self.public_message
        self.details = details or {}


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404
    public_message = "请求的资源不存在。"


class ValidationAppError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422
    public_message = "请求内容无法通过校验。"


class IdempotencyConflictError(AppError):
    code = "IDEMPOTENCY_CONFLICT"
    status_code = 409
    public_message = "该幂等键已用于不同的请求。"


class VersionConflictError(AppError):
    code = "VERSION_CONFLICT"
    status_code = 409
    public_message = "路书基础版本已变化，请基于最新版本重试。"


class DependencyUnavailableError(AppError):
    code = "DEPENDENCY_UNAVAILABLE"
    status_code = 503
    public_message = "依赖服务暂时不可用，请稍后重试。"
