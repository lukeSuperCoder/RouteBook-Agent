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
            self.client.publish(
                progress_channel(event.workflow_run_id),
                event.model_dump_json(),
            )
        except redis.RedisError:
            log.exception(
                "progress publish failed run_id=%s stage=%s",
                event.workflow_run_id,
                event.stage,
            )


async def stream_progress(redis_url: str, run_id: UUID) -> AsyncIterator[str]:
    client = async_redis.Redis.from_url(redis_url, decode_responses=True)
    pubsub = client.pubsub()
    terminal_statuses = {WorkflowStatus.COMPLETED.value, WorkflowStatus.FAILED.value}
    heartbeat_elapsed = 0.0
    try:
        await pubsub.subscribe(progress_channel(run_id))
        yield ": connected\n\n"
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
