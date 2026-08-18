from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import redis
import redis.asyncio as async_redis

from .enums import WorkflowStage, WorkflowStatus
from .schemas import ProgressEvent, ProgressValue

log = logging.getLogger("routebook.progress")


def progress_channel(run_id: UUID) -> str:
    return f"routebook:workflow:{run_id}:events"


def progress_stream(run_id: UUID) -> str:
    return f"routebook:workflow:{run_id}:event-stream"


def build_progress_event(
    *,
    run_id: UUID,
    routebook_id: UUID,
    stage: WorkflowStage,
    status: WorkflowStatus,
    message: str,
    completed: int,
    total: int,
) -> ProgressEvent:
    return ProgressEvent(
        event_id=uuid4(),
        workflow_run_id=run_id,
        routebook_id=routebook_id,
        stage=stage,
        status=status,
        message=message,
        progress=ProgressValue(completed=completed, total=total),
        occurred_at=datetime.now(UTC),
    )


class ProgressPublisher:
    def __init__(self, redis_url: str) -> None:
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def publish(self, event: ProgressEvent) -> None:
        try:
            # Persist first so refresh recovery does not depend on Redis availability.
            from .db import SessionFactory
            from .repositories import WorkflowRunRepository

            with SessionFactory.begin() as session:
                run = WorkflowRunRepository(session).get(event.workflow_run_id, for_update=True)
                if run is not None:
                    run.current_stage = event.stage.value
                    run.status = event.status.value
                    run.status_message = event.message
                    run.latest_event_id = str(event.event_id)
        except Exception:
            log.exception("progress snapshot persistence failed")
        try:
            data = event.model_dump_json()
            pipe = self.client.pipeline()
            pipe.xadd(
                progress_stream(event.workflow_run_id),
                {"event": data},
                maxlen=1000,
                approximate=True,
            )
            pipe.publish(progress_channel(event.workflow_run_id), data)
            pipe.execute()
        except redis.RedisError:
            log.exception(
                "progress publish failed run_id=%s stage=%s",
                event.workflow_run_id,
                event.stage,
            )


async def stream_progress(
    redis_url: str,
    run_id: UUID,
    *,
    last_event_id: str | None = None,
    snapshot: ProgressEvent | None = None,
) -> AsyncIterator[str]:
    client = async_redis.Redis.from_url(redis_url, decode_responses=True)
    pubsub = client.pubsub()
    terminal_statuses = {
        WorkflowStatus.COMPLETED.value,
        WorkflowStatus.FAILED.value,
        WorkflowStatus.CANCELLED.value,
        WorkflowStatus.INTERRUPTED.value,
    }
    heartbeat_elapsed = 0.0
    try:
        await pubsub.subscribe(progress_channel(run_id))
        if snapshot is not None:
            data = snapshot.model_dump_json()
            yield f"id: {snapshot.event_id}\nevent: snapshot\ndata: {data}\n\n"
            if snapshot.status.value in terminal_statuses and not last_event_id:
                return
        entries = await client.xrange(progress_stream(run_id), min="-", max="+")
        replaying = last_event_id is None
        for _, fields in entries:
            event = ProgressEvent.model_validate_json(fields["event"])
            if not replaying:
                replaying = str(event.event_id) == last_event_id
                continue
            data = event.model_dump_json()
            yield f"id: {event.event_id}\nevent: progress\ndata: {data}\n\n"
            if event.status.value in terminal_statuses:
                return
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                data = str(message["data"])
                event = ProgressEvent.model_validate_json(data)
                yield f"id: {event.event_id}\nevent: progress\ndata: {data}\n\n"
                if event.status.value in terminal_statuses:
                    return
                heartbeat_elapsed = 0.0
            else:
                heartbeat_elapsed += 1.0
                if heartbeat_elapsed >= 15.0:
                    yield ": heartbeat\n\n"
                    heartbeat_elapsed = 0.0
                await asyncio.sleep(0)
    finally:
        await pubsub.unsubscribe(progress_channel(run_id))
        await pubsub.aclose()
        await client.aclose()
