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
from .editing.graph import invoke_editing_subgraph
from .editing.models import EditIntent, ImpactScope
from .editing.persistence import EditingPersistenceService
from .editing.recompute import AffectedScopeRecomputer
from .enums import PlanningPhase, RouteBookStatus, WorkflowRunType, WorkflowStage, WorkflowStatus
from .errors import (
    AppError,
    DependencyUnavailableError,
    NotFoundError,
    ValidationAppError,
    WorkflowStateConflictError,
)
from .finalization import FinalizationService
from .models import ConversationMessageModel, WorkflowRunModel, utc_now
from .observability import configure_logging, request_id_context
from .planning.graph import invoke_itinerary_planning_subgraph
from .planning.models import PlanningPlace, PlanningResult
from .planning.persistence import PlanningPersistenceService
from .planning.service import ItineraryPlanningService
from .presenters import (
    conversation_message_read,
    proposal_read,
    routebook_read,
    version_read,
    workflow_run_read,
)
from .progress import ProgressPublisher, build_progress_event, stream_progress
from .providers.amap import AmapAdapter
from .providers.models import Coordinate, RouteResult
from .providers.qweather import QWeatherAdapter
from .recommendations.models import PlaceFeedback, RecommendationResult
from .recommendations.persistence import RecommendationPersistenceService, present_batch
from .recommendations.service import RecommendationService
from .recommendations.strategy import build_recommendation_strategy
from .repositories import (
    ConversationMessageRepository,
    ProposalRepository,
    RecommendationRepository,
    RouteBookRepository,
    VersionRepository,
    WorkflowRunRepository,
)
from .schemas import (
    AsyncWorkflowAccepted,
    ConversationMessageRead,
    CreateRouteBookRequest,
    EditExecutionRead,
    ErrorResponse,
    FinalizeRouteBookRead,
    FinalizeRouteBookRequest,
    HealthResponse,
    PlaceFeedbackRequest,
    ProposalDecisionRequest,
    ProposalRead,
    RecommendationBatchRead,
    RecommendationGenerateRequest,
    RecommendationObservabilityRead,
    RequirementResumeRequest,
    RequirementSnapshot,
    RequirementWorkflowAccepted,
    RevokeFinalPageRead,
    RouteBookCreationAccepted,
    RouteBookEditRequest,
    RouteBookMessageCreate,
    RouteBookRead,
    RouteBookSnapshotV1,
    RouteBookVersionRead,
    SharedRouteBookRead,
    UndoRequest,
    WorkflowRunRead,
)
from .services import (
    MessageWorkflowResult,
    RequirementMessageService,
    RouteBookService,
    canonical_request_hash,
)
from .worker import (
    cancel_workflow,
    dispatch_itinerary_workflow,
    dispatch_recommendation_workflow,
    dispatch_requirement_workflow,
    dispatch_workflow,
)

settings = get_settings()
log = logging.getLogger("routebook.api")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
WorkflowDispatcher = Callable[[UUID, str], None]
RequirementWorkflowDispatcher = Callable[[UUID, UUID, str, bool], None]
RecommendationWorkflowDispatcher = Callable[[UUID, str, int], None]
ItineraryWorkflowDispatcher = Callable[[UUID, str], None]
RecommendationRunner = Callable[
    [RequirementSnapshot, int, list[PlaceFeedback]], RecommendationResult
]
ItineraryRunner = Callable[[RequirementSnapshot, list[PlanningPlace]], PlanningResult]
EditRecomputeRunner = Callable[[RouteBookSnapshotV1, ImpactScope], RouteBookSnapshotV1]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    yield


def _dispatch_requirement(run_id: UUID, message_id: UUID, request_id: str, resume: bool) -> None:
    dispatch_requirement_workflow(run_id, message_id, request_id, resume=resume)


def _run_recommendations(
    requirements: RequirementSnapshot, limit: int, feedback: list[PlaceFeedback]
) -> RecommendationResult:
    rejected_reasons = [
        item.reason.value
        for item in feedback
        if item.action in {"reject", "replace"} and item.reason is not None
    ]
    strategy = build_recommendation_strategy(requirements, rejected_reasons=rejected_reasons)
    amap = AmapAdapter()
    service = RecommendationService(lambda query, region: amap.search_places(query, region=region))
    return service.recommend(strategy, limit=limit, feedback=feedback)


def _run_itinerary(
    requirements: RequirementSnapshot, places: list[PlanningPlace]
) -> PlanningResult:
    amap = AmapAdapter()
    weather = QWeatherAdapter()

    def route(origin: Coordinate, destination: Coordinate, mode: str) -> RouteResult:
        if mode == "walking":
            return amap.walking_route(origin, destination)
        return amap.driving_route(origin, destination)

    service = ItineraryPlanningService(
        route_fetcher=route,
        weather_fetcher=weather.daily_forecast,
        warning_fetcher=weather.warnings,
    )
    return invoke_itinerary_planning_subgraph(service, requirements, places)


def _recompute_edit_scope(
    snapshot: RouteBookSnapshotV1, impact: ImpactScope
) -> RouteBookSnapshotV1:
    amap = AmapAdapter()
    weather = QWeatherAdapter()

    def route(origin: Coordinate, destination: Coordinate, mode: str) -> RouteResult:
        if mode == "walking":
            return amap.walking_route(origin, destination)
        return amap.driving_route(origin, destination)

    return AffectedScopeRecomputer(route, weather.daily_forecast).recompute(snapshot, impact)


def create_app(
    workflow_dispatcher: WorkflowDispatcher = dispatch_workflow,
    requirement_dispatcher: RequirementWorkflowDispatcher = _dispatch_requirement,
    recommendation_runner: RecommendationRunner = _run_recommendations,
    itinerary_runner: ItineraryRunner = _run_itinerary,
    edit_recompute_runner: EditRecomputeRunner = _recompute_edit_scope,
    recommendation_dispatcher: RecommendationWorkflowDispatcher = dispatch_recommendation_workflow,
    itinerary_dispatcher: ItineraryWorkflowDispatcher = dispatch_itinerary_workflow,
) -> FastAPI:
    app = FastAPI(
        title="RouteBook Agent API",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.workflow_dispatcher = workflow_dispatcher
    app.state.requirement_dispatcher = requirement_dispatcher
    app.state.recommendation_runner = recommendation_runner
    app.state.itinerary_runner = itinerary_runner
    app.state.recommendation_dispatcher = recommendation_dispatcher
    app.state.itinerary_dispatcher = itinerary_dispatcher
    app.state.edit_recompute_runner = edit_recompute_runner
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

    @app.post(
        "/api/routebooks/{routebook_id}/messages",
        response_model=RequirementWorkflowAccepted,
        status_code=202,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["messages"],
    )
    def create_routebook_message(
        routebook_id: UUID,
        payload: RouteBookMessageCreate,
        request: Request,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> RequirementWorkflowAccepted:
        with session.begin():
            result = RequirementMessageService.start(
                session,
                routebook_id=routebook_id,
                client_message_id=payload.message_id,
                text=payload.text,
            )
        if result.should_dispatch:
            try:
                request.app.state.requirement_dispatcher(
                    result.workflow_run_id, result.message.id, request.state.request_id, False
                )
            except Exception as exc:
                log.exception(
                    "requirement workflow dispatch failed run_id=%s",
                    result.workflow_run_id,
                )
                raise DependencyUnavailableError(details={"dependency": "celery_broker"}) from exc
        return _requirement_accepted(result)

    @app.get(
        "/api/routebooks/{routebook_id}/messages",
        response_model=list[ConversationMessageRead],
        responses={404: {"model": ErrorResponse}},
        tags=["messages"],
    )
    def get_routebook_messages(
        routebook_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> list[ConversationMessageRead]:
        if RouteBookRepository(session).get(routebook_id) is None:
            raise NotFoundError(details={"resource": "routebook"})
        return [
            conversation_message_read(model)
            for model in ConversationMessageRepository(session).list_for_routebook(routebook_id)
        ]

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
        "/api/routebooks/{routebook_id}/versions",
        response_model=list[RouteBookVersionRead],
        responses={404: {"model": ErrorResponse}},
        tags=["routebooks"],
    )
    def list_versions(
        routebook_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> list[RouteBookVersionRead]:
        if RouteBookRepository(session).get(routebook_id) is None:
            raise NotFoundError(details={"resource": "routebook"})
        return [
            version_read(item)
            for item in VersionRepository(session).list_for_routebook(routebook_id)
        ]

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
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> StreamingResponse:
        run = WorkflowRunRepository(session).get(run_id)
        if run is None:
            raise NotFoundError(details={"resource": "workflow_run"})
        snapshot = build_progress_event(
            run_id=run.id,
            routebook_id=run.routebook_id,
            stage=WorkflowStage(run.current_stage),
            status=WorkflowStatus(run.status),
            message=run.status_message or "工作流状态已恢复",
            completed=1 if run.status != WorkflowStatus.QUEUED.value else 0,
            total=1,
        )
        if run.latest_event_id:
            snapshot = snapshot.model_copy(update={"event_id": UUID(run.latest_event_id)})
        resume_after = last_event_id
        if (
            resume_after is None
            and run.latest_event_id
            and run.status in {WorkflowStatus.QUEUED.value, WorkflowStatus.RUNNING.value}
        ):
            # A resumed requirement workflow reuses its run id and Redis stream.
            # Skip the previous interrupted terminal event instead of replaying it
            # and closing the new SSE connection before fresh progress arrives.
            resume_after = run.latest_event_id
        return StreamingResponse(
            stream_progress(
                settings.redis_url, run_id, last_event_id=resume_after, snapshot=snapshot
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(
        "/api/routebooks/{routebook_id}/workflow-runs/active",
        response_model=list[WorkflowRunRead],
        tags=["workflows"],
    )
    def active_workflow_runs(
        routebook_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> list[WorkflowRunRead]:
        if RouteBookRepository(session).get(routebook_id) is None:
            raise NotFoundError(details={"resource": "routebook"})
        return [
            workflow_run_read(run)
            for run in WorkflowRunRepository(session).active_for_routebook(routebook_id)
        ]

    @app.post(
        "/api/workflow-runs/{run_id}/cancel", response_model=WorkflowRunRead, tags=["workflows"]
    )
    def cancel_active_workflow(
        run_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> WorkflowRunRead:
        with session.begin():
            run = WorkflowRunRepository(session).get(run_id, for_update=True)
            if run is None:
                raise NotFoundError(details={"resource": "workflow_run"})
            if run.status not in {WorkflowStatus.QUEUED.value, WorkflowStatus.RUNNING.value}:
                raise WorkflowStateConflictError(details={"status": run.status})
            run.status = WorkflowStatus.CANCELLED.value
            run.status_message = "任务已取消"
            run.completed_at = utc_now()
        cancel_workflow(run_id)
        ProgressPublisher(settings.redis_url).publish(
            build_progress_event(
                run_id=run.id,
                routebook_id=run.routebook_id,
                stage=WorkflowStage(run.current_stage),
                status=WorkflowStatus.CANCELLED,
                message="任务已取消",
                completed=0,
                total=1,
            )
        )
        return workflow_run_read(run)

    @app.post(
        "/api/workflow-runs/{run_id}/resume",
        response_model=RequirementWorkflowAccepted,
        status_code=202,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["workflows"],
    )
    def resume_requirement_workflow(
        run_id: UUID,
        payload: RequirementResumeRequest,
        request: Request,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> RequirementWorkflowAccepted:
        with session.begin():
            result = RequirementMessageService.resume(
                session,
                run_id=run_id,
                client_message_id=payload.message_id,
                text=payload.text,
            )
        if result.should_dispatch:
            try:
                request.app.state.requirement_dispatcher(
                    result.workflow_run_id, result.message.id, request.state.request_id, True
                )
            except Exception as exc:
                log.exception("requirement resume dispatch failed run_id=%s", run_id)
                raise DependencyUnavailableError(details={"dependency": "celery_broker"}) from exc
        return _requirement_accepted(result)

    @app.post(
        "/api/routebooks/{routebook_id}/recommendations",
        response_model=AsyncWorkflowAccepted,
        status_code=202,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["recommendations"],
    )
    def generate_recommendations(
        routebook_id: UUID,
        payload: RecommendationGenerateRequest,
        request: Request,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> AsyncWorkflowAccepted:
        with session.begin():
            base_version_id, _ = RecommendationPersistenceService.confirmed_requirements(
                session, routebook_id
            )
            if any(
                item.run_type in {WorkflowRunType.RECOMMEND.value, WorkflowRunType.PLAN.value}
                for item in WorkflowRunRepository(session).active_for_routebook(routebook_id)
            ):
                raise WorkflowStateConflictError(details={"reason": "workflow_already_active"})
            run = WorkflowRunModel(
                routebook_id=routebook_id,
                run_type=WorkflowRunType.RECOMMEND.value,
                base_version_id=base_version_id,
                status=WorkflowStatus.QUEUED.value,
                current_stage=WorkflowStage.QUEUED.value,
                phase=PlanningPhase.GENERATING_RECOMMENDATIONS.value,
                status_message="推荐任务已进入队列",
            )
            WorkflowRunRepository(session).add(run)
            session.flush()
        request.app.state.recommendation_dispatcher(run.id, request.state.request_id, payload.limit)
        return AsyncWorkflowAccepted(
            workflow_run_id=run.id,
            workflow_status=WorkflowStatus.QUEUED,
            status_url=f"/api/workflow-runs/{run.id}",
            events_url=f"/api/workflow-runs/{run.id}/events",
        )

    @app.get(
        "/api/routebooks/{routebook_id}/recommendations/latest",
        response_model=RecommendationBatchRead,
        responses={404: {"model": ErrorResponse}},
        tags=["recommendations"],
    )
    def get_latest_recommendations(
        routebook_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> RecommendationBatchRead:
        return RecommendationPersistenceService.latest(session, routebook_id)

    @app.get(
        "/api/routebooks/{routebook_id}/recommendations/metrics",
        response_model=RecommendationObservabilityRead,
        responses={404: {"model": ErrorResponse}},
        tags=["recommendations"],
    )
    def get_recommendation_metrics(
        routebook_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> RecommendationObservabilityRead:
        return RecommendationPersistenceService.observability(session, routebook_id)

    @app.post(
        "/api/routebooks/{routebook_id}/recommendations/{proposal_id}/feedback",
        response_model=RecommendationBatchRead,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["recommendations"],
    )
    def submit_place_feedback(
        routebook_id: UUID,
        proposal_id: UUID,
        payload: PlaceFeedbackRequest,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> RecommendationBatchRead:
        with session.begin():
            proposal = RecommendationPersistenceService.apply_feedback(
                session,
                routebook_id=routebook_id,
                proposal_id=proposal_id,
                feedback=PlaceFeedback(
                    provider_place_id=str(proposal_id),
                    action=payload.action,
                    reason=payload.reason,
                    note=payload.note,
                ),
            )
        if payload.action == "replace":
            with session.begin():
                base_version_id, snapshot = RecommendationPersistenceService.confirmed_requirements(
                    session, routebook_id
                )
                feedback = RecommendationPersistenceService.feedback_history(session, routebook_id)
            result = app.state.recommendation_runner(snapshot.requirements, 8, feedback)
            with session.begin():
                replacement_batch = RecommendationPersistenceService.save(
                    session,
                    routebook_id=routebook_id,
                    base_version_id=base_version_id,
                    result=result,
                )
            return present_batch(
                replacement_batch,
                RecommendationRepository(session).list_proposals(replacement_batch.id),
            )
        repository = RecommendationRepository(session)
        batch = repository.latest_batch(routebook_id)
        if batch is None or proposal.batch_id != batch.id:
            raise NotFoundError(details={"resource": "recommendation_batch"})
        return present_batch(batch, repository.list_proposals(batch.id))

    @app.post(
        "/api/routebooks/{routebook_id}/itinerary",
        response_model=AsyncWorkflowAccepted,
        status_code=202,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["planning"],
    )
    def generate_itinerary(
        routebook_id: UUID,
        request: Request,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> AsyncWorkflowAccepted:
        with session.begin():
            base_version_id, _, _ = PlanningPersistenceService.load_input(session, routebook_id)
            if any(
                item.run_type in {WorkflowRunType.RECOMMEND.value, WorkflowRunType.PLAN.value}
                for item in WorkflowRunRepository(session).active_for_routebook(routebook_id)
            ):
                raise WorkflowStateConflictError(details={"reason": "workflow_already_active"})
            run = WorkflowRunModel(
                routebook_id=routebook_id,
                run_type=WorkflowRunType.PLAN.value,
                base_version_id=base_version_id,
                status=WorkflowStatus.QUEUED.value,
                current_stage=WorkflowStage.QUEUED.value,
                phase=PlanningPhase.BUILDING_ITINERARY.value,
                status_message="行程任务已进入队列",
            )
            WorkflowRunRepository(session).add(run)
            session.flush()
        request.app.state.itinerary_dispatcher(run.id, request.state.request_id)
        return AsyncWorkflowAccepted(
            workflow_run_id=run.id,
            workflow_status=WorkflowStatus.QUEUED,
            status_url=f"/api/workflow-runs/{run.id}",
            events_url=f"/api/workflow-runs/{run.id}/events",
        )

    @app.post(
        "/api/routebooks/{routebook_id}/edits",
        response_model=EditExecutionRead,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["editing"],
    )
    def edit_routebook(
        routebook_id: UUID,
        payload: RouteBookEditRequest,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> EditExecutionRead:
        with session.begin():
            base_version_id, snapshot = EditingPersistenceService.load_current(
                session, routebook_id
            )
        intent = EditIntent.model_validate(
            payload.model_dump(exclude={"operation_id"}, mode="python")
        )
        plan = invoke_editing_subgraph(snapshot, intent)
        if not plan.resolution.resolved:
            return EditExecutionRead(
                status="needs_clarification",
                clarification=plan.resolution.clarification,
                candidates=plan.resolution.candidates,
            )
        if plan.preview is not None and intent.operation in {
            "add_place",
            "remove_place",
            "replace_place",
            "change_days",
            "edit_day",
        }:
            recomputed = app.state.edit_recompute_runner(plan.preview, plan.impact)
            plan = plan.model_copy(update={"preview": recomputed})
        with session.begin():
            result = EditingPersistenceService.execute(
                session,
                routebook_id=routebook_id,
                base_version_id=base_version_id,
                base_snapshot=snapshot,
                plan=plan,
                operation_id=payload.operation_id,
            )
            messages = ConversationMessageRepository(session)
            user_message_key = f"edit-user-{payload.operation_id}"
            if messages.get_by_client_id(routebook_id, user_message_key) is None:
                messages.add(
                    ConversationMessageModel(
                        routebook_id=routebook_id,
                        workflow_run_id=payload.operation_id,
                        client_message_id=user_message_key,
                        role="user",
                        kind="status",
                        payload_jsonb={"text": payload.note or "提交了一项行程修改"},
                    )
                )
            assistant_message_key = f"edit-assistant-{payload.operation_id}"
            if messages.get_by_client_id(routebook_id, assistant_message_key) is None:
                day_label = payload.day_reference or "行程"
                reply = (
                    f"{day_label}的修改方案已生成，请在右侧预览并确认。"
                    if result.proposal
                    else f"已按你的要求重新规划{day_label}，地点顺序和路线数据已同步刷新。"
                )
                messages.add(
                    ConversationMessageModel(
                        routebook_id=routebook_id,
                        workflow_run_id=payload.operation_id,
                        client_message_id=assistant_message_key,
                        role="assistant",
                        kind="status",
                        payload_jsonb={"text": reply},
                    )
                )
        return EditExecutionRead(
            status="pending_confirmation" if result.proposal else "completed",
            version_id=result.version_id,
            proposal=proposal_read(result.proposal) if result.proposal else None,
            reused=result.reused,
        )

    @app.get(
        "/api/routebooks/{routebook_id}/proposals",
        response_model=list[ProposalRead],
        responses={404: {"model": ErrorResponse}},
        tags=["editing"],
    )
    def list_proposals(
        routebook_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> list[ProposalRead]:
        if RouteBookRepository(session).get(routebook_id) is None:
            raise NotFoundError(details={"resource": "routebook"})
        return [
            proposal_read(item)
            for item in ProposalRepository(session).list_for_routebook(routebook_id)
        ]

    @app.post(
        "/api/proposals/{proposal_id}/decision",
        response_model=EditExecutionRead,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["editing"],
    )
    def decide_proposal(
        proposal_id: UUID,
        payload: ProposalDecisionRequest,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> EditExecutionRead:
        with session.begin():
            result = EditingPersistenceService.resolve_proposal(
                session, proposal_id=proposal_id, accept=payload.decision == "accept"
            )
        return EditExecutionRead(
            status="completed",
            version_id=result.version_id,
            proposal=proposal_read(result.proposal) if result.proposal else None,
            reused=result.reused,
        )

    @app.post(
        "/api/routebooks/{routebook_id}/undo",
        response_model=EditExecutionRead,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["editing"],
    )
    def undo_routebook(
        routebook_id: UUID,
        payload: UndoRequest,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> EditExecutionRead:
        with session.begin():
            result = EditingPersistenceService.undo(
                session, routebook_id=routebook_id, operation_id=payload.operation_id
            )
        return EditExecutionRead(
            status="completed", version_id=result.version_id, reused=result.reused
        )

    @app.post(
        "/api/routebooks/{routebook_id}/finalize",
        response_model=FinalizeRouteBookRead,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
        tags=["finalization"],
    )
    def finalize_routebook(
        routebook_id: UUID,
        payload: FinalizeRouteBookRequest,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> FinalizeRouteBookRead:
        with session.begin():
            result = FinalizationService.finalize(
                session,
                routebook_id=routebook_id,
                version_id=payload.routebook_version_id,
                privacy_policy=payload.privacy_policy,
            )
        page = result.final_page
        return FinalizeRouteBookRead(
            final_page_id=page.id,
            routebook_id=page.routebook_id,
            routebook_version_id=page.routebook_version_id,
            public_token=result.public_token,
            share_url=f"/share/{result.public_token}",
            privacy_policy=page.privacy_policy,
            created_at=page.created_at,
        )

    @app.get(
        "/share/{public_token}",
        response_model=SharedRouteBookRead,
        responses={404: {"model": ErrorResponse}},
        tags=["finalization"],
    )
    def get_shared_routebook(
        public_token: str,
        session: Session = Depends(get_session),
    ) -> SharedRouteBookRead:
        if not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", public_token):
            raise NotFoundError(details={"resource": "shared_routebook"})
        return FinalizationService.load_shared(session, public_token)

    @app.post(
        "/api/routebooks/{routebook_id}/final-pages/{final_page_id}/revoke",
        response_model=RevokeFinalPageRead,
        responses={404: {"model": ErrorResponse}},
        tags=["finalization"],
    )
    def revoke_final_page(
        routebook_id: UUID,
        final_page_id: UUID,
        session: Session = Depends(get_session),
        _principal: RequestPrincipal = Depends(get_request_principal),
    ) -> RevokeFinalPageRead:
        with session.begin():
            revoked_at = FinalizationService.revoke(
                session, routebook_id=routebook_id, final_page_id=final_page_id
            )
        return RevokeFinalPageRead(final_page_id=final_page_id, revoked_at=revoked_at)

    return app


def _requirement_accepted(result: MessageWorkflowResult) -> RequirementWorkflowAccepted:
    run_id = result.workflow_run_id
    return RequirementWorkflowAccepted(
        message=conversation_message_read(result.message),
        workflow_run_id=run_id,
        workflow_status=result.workflow_status,
        reused=result.reused,
        status_url=f"/api/workflow-runs/{run_id}",
        events_url=f"/api/workflow-runs/{run_id}/events",
    )


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
