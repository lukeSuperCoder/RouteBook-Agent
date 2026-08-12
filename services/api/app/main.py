from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import redis
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from . import __version__
from .auth import RequestPrincipal, get_request_principal
from .config import get_settings
from .db import get_session
from .enums import RouteBookStatus, WorkflowStage, WorkflowStatus
from .errors import (
    AppError,
    DependencyUnavailableError,
    NotFoundError,
    ValidationAppError,
)
from .observability import configure_logging, request_id_context
from .presenters import routebook_read, version_read, workflow_run_read
from .progress import ProgressPublisher, build_progress_event, stream_progress
from .repositories import RouteBookRepository, VersionRepository, WorkflowRunRepository
from .schemas import (
    CreateRouteBookRequest,
    ErrorResponse,
    HealthResponse,
    RouteBookCreationAccepted,
    RouteBookRead,
    RouteBookVersionRead,
    WorkflowRunRead,
)
from .services import RouteBookService, canonical_request_hash
from .worker import dispatch_workflow

settings = get_settings()
log = logging.getLogger("routebook.api")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
WorkflowDispatcher = Callable[[UUID, str], None]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    yield


def create_app(workflow_dispatcher: WorkflowDispatcher = dispatch_workflow) -> FastAPI:
    app = FastAPI(
        title="RouteBook Agent API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.workflow_dispatcher = workflow_dispatcher
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if 1 <= len(supplied) <= 128 else str(uuid4())
        request.state.request_id = request_id
        token = request_id_context.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_context.reset(token)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return _error_response(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details: dict[str, object] = {"errors": _serializable_validation_errors(exc)}
        return _error_response(
            request,
            422,
            "VALIDATION_ERROR",
            "请求内容无法通过校验。",
            details,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled API error", exc_info=exc)
        return _error_response(
            request,
            500,
            "INTERNAL_ERROR",
            "服务暂时不可用，请稍后重试。",
            {},
        )

    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    def live() -> HealthResponse:
        return HealthResponse(status="ok", checks={"api": "ok"})

    @app.get(
        "/health/ready",
        response_model=HealthResponse,
        responses={503: {"model": HealthResponse}},
        tags=["health"],
    )
    def ready(session: Session = Depends(get_session)) -> HealthResponse | JSONResponse:
        checks: dict[str, str] = {}
        try:
            session.execute(text("SELECT 1"))
            business_table = session.scalar(text("SELECT to_regclass('routebook.routebooks')"))
            checkpoint_table = session.scalar(text("SELECT to_regclass('langgraph.checkpoints')"))
            checks["postgres"] = "ok"
            checks["migrations"] = "ok" if business_table else "missing"
            checks["checkpoint"] = "ok" if checkpoint_table else "missing"
        except SQLAlchemyError:
            log.exception("readiness postgres check failed")
            checks.update(postgres="unavailable", migrations="unknown", checkpoint="unknown")
        try:
            client = redis.Redis.from_url(settings.redis_url, socket_timeout=1)
            checks["redis"] = "ok" if client.ping() else "unavailable"
            client.close()
        except redis.RedisError:
            log.exception("readiness redis check failed")
            checks["redis"] = "unavailable"
        is_ready = all(value == "ok" for value in checks.values())
        payload = HealthResponse(status="ready" if is_ready else "not_ready", checks=checks)
        if not is_ready:
            return JSONResponse(status_code=503, content=payload.model_dump(mode="json"))
        return payload

    @app.post(
        "/api/routebooks",
        response_model=RouteBookCreationAccepted,
        status_code=202,
        responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        tags=["routebooks"],
    )
    def create_routebook(
        payload: CreateRouteBookRequest,
        request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> RouteBookCreationAccepted:
        if not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise ValidationAppError(
                "Idempotency-Key 必须为 8～128 位安全字符。",
                details={"field": "Idempotency-Key"},
            )
        request_hash = canonical_request_hash(payload.model_dump(mode="json"))
        with session.begin():
            result = RouteBookService.create(
                session,
                title=payload.title,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        try:
            request.app.state.workflow_dispatcher(result.workflow_run_id, request.state.request_id)
        except Exception as exc:
            log.exception("workflow dispatch failed run_id=%s", result.workflow_run_id)
            raise DependencyUnavailableError(details={"dependency": "celery_broker"}) from exc
        ProgressPublisher(settings.redis_url).publish(
            build_progress_event(
                run_id=result.workflow_run_id,
                routebook_id=result.routebook_id,
                stage=WorkflowStage.QUEUED,
                status=WorkflowStatus.QUEUED,
                message="工作流已进入队列",
                completed=0,
                total=2,
            )
        )
        return RouteBookCreationAccepted(
            routebook_id=result.routebook_id,
            workflow_run_id=result.workflow_run_id,
            routebook_status=RouteBookStatus.DRAFT,
            workflow_status=WorkflowStatus.QUEUED,
            status_url=f"/api/workflow-runs/{result.workflow_run_id}",
            events_url=f"/api/workflow-runs/{result.workflow_run_id}/events",
        )

    @app.get(
        "/api/routebooks/{routebook_id}",
        response_model=RouteBookRead,
        responses={404: {"model": ErrorResponse}},
        tags=["routebooks"],
    )
    def get_routebook(
        routebook_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> RouteBookRead:
        model = RouteBookRepository(session).get(routebook_id)
        if model is None:
            raise NotFoundError(details={"resource": "routebook"})
        current = (
            VersionRepository(session).get(model.current_version_id)
            if model.current_version_id
            else None
        )
        return routebook_read(model, current)

    @app.get(
        "/api/routebooks/{routebook_id}/versions/{version_id}",
        response_model=RouteBookVersionRead,
        responses={404: {"model": ErrorResponse}},
        tags=["routebooks"],
    )
    def get_version(
        routebook_id: UUID,
        version_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> RouteBookVersionRead:
        model = VersionRepository(session).get(version_id)
        if model is None or model.routebook_id != routebook_id:
            raise NotFoundError(details={"resource": "routebook_version"})
        return version_read(model)

    @app.get(
        "/api/workflow-runs/{run_id}",
        response_model=WorkflowRunRead,
        responses={404: {"model": ErrorResponse}},
        tags=["workflows"],
    )
    def get_workflow_run(
        run_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> WorkflowRunRead:
        model = WorkflowRunRepository(session).get(run_id)
        if model is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        return workflow_run_read(model)

    @app.get("/api/workflow-runs/{run_id}/events", tags=["workflows"])
    def workflow_events(
        run_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> StreamingResponse:
        if WorkflowRunRepository(session).get(run_id) is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        return StreamingResponse(
            stream_progress(settings.redis_url, run_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, object],
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "details": details,
            }
        },
    )


def _serializable_validation_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    serialized: list[dict[str, object]] = []
    for error in exc.errors():
        serialized.append(
            {
                "type": str(error.get("type", "validation_error")),
                "loc": [str(item) for item in error.get("loc", ())],
                "msg": str(error.get("msg", "invalid value")),
            }
        )
    return serialized


app = create_app()
