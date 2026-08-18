from __future__ import annotations

from uuid import uuid4

import pytest

from services.api.app.enums import WorkflowStage, WorkflowStatus
from services.api.app.progress import build_progress_event, stream_progress


class FakePubSub:
    async def subscribe(self, _channel: str) -> None:
        pass

    async def unsubscribe(self, _channel: str) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def get_message(self, **_: object) -> None:
        return None


class FakeRedis:
    def __init__(self, entries: list[tuple[str, dict[str, str]]]) -> None:
        self.entries = entries

    def pubsub(self) -> FakePubSub:
        return FakePubSub()

    async def xrange(self, *_: object, **__: object) -> list[tuple[str, dict[str, str]]]:
        return self.entries

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_progress_stream_replays_events_after_last_event_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    routebook_id = uuid4()
    started = build_progress_event(
        run_id=run_id,
        routebook_id=routebook_id,
        stage=WorkflowStage.PLANNING_DAYS,
        status=WorkflowStatus.RUNNING,
        message="正在编排",
        completed=1,
        total=2,
    )
    completed = build_progress_event(
        run_id=run_id,
        routebook_id=routebook_id,
        stage=WorkflowStage.COMPLETED,
        status=WorkflowStatus.COMPLETED,
        message="已完成",
        completed=2,
        total=2,
    )
    fake = FakeRedis(
        [
            ("1-0", {"event": started.model_dump_json()}),
            ("2-0", {"event": completed.model_dump_json()}),
        ]
    )
    monkeypatch.setattr(
        "services.api.app.progress.async_redis.Redis.from_url", lambda *_a, **_k: fake
    )

    chunks = [
        chunk
        async for chunk in stream_progress(
            "redis://unused", run_id, last_event_id=str(started.event_id)
        )
    ]

    assert len(chunks) == 1
    assert f"id: {completed.event_id}" in chunks[0]
    assert '"status":"completed"' in chunks[0]


@pytest.mark.asyncio
async def test_interrupted_is_a_terminal_sse_state(monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = uuid4()
    interrupted = build_progress_event(
        run_id=run_id,
        routebook_id=uuid4(),
        stage=WorkflowStage.WAITING_FOR_CLARIFICATION,
        status=WorkflowStatus.INTERRUPTED,
        message="等待补充信息",
        completed=1,
        total=2,
    )
    fake = FakeRedis([("1-0", {"event": interrupted.model_dump_json()})])
    monkeypatch.setattr(
        "services.api.app.progress.async_redis.Redis.from_url", lambda *_a, **_k: fake
    )

    chunks = [chunk async for chunk in stream_progress("redis://unused", run_id)]

    assert len(chunks) == 1
    assert '"status":"interrupted"' in chunks[0]
