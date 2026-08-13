from uuid import uuid4

from services.api.app.config import Settings
from services.api.app.enums import WorkflowStage, WorkflowStatus
from services.api.app.observability import redact
from services.api.app.progress import build_progress_event
from services.api.app.schemas import RouteBookSnapshotV1
from services.api.app.services import canonical_request_hash


def test_empty_snapshot_is_canonical_and_versioned() -> None:
    snapshot = RouteBookSnapshotV1()

    assert snapshot.schema_version == 1
    assert snapshot.requirements.destination.source.value == "missing"
    assert snapshot.places == []
    assert snapshot.days_plan == []
    assert snapshot.route_segments == []
    assert snapshot.weather == []


def test_cors_origins_accept_compose_comma_separated_environment(monkeypatch) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", "http://localhost:3000,https://example.test")
    settings = Settings(_env_file=None)
    assert settings.api_cors_origins == [
        "http://localhost:3000",
        "https://example.test",
    ]


def test_request_hash_is_stable_across_key_order() -> None:
    assert canonical_request_hash({"title": "武汉", "extra": 1}) == canonical_request_hash(
        {"extra": 1, "title": "武汉"}
    )


def test_sensitive_logging_fields_are_redacted() -> None:
    assert redact(
        {
            "api_key": "secret",
            "nested": {"authorization": "Bearer x"},
            "url": "https://example.test/path?key=secret-value&output=json",
        }
    ) == {
        "api_key": "[REDACTED]",
        "nested": {"authorization": "[REDACTED]"},
        "url": "https://example.test/path?key=[REDACTED]&output=json",
    }


def test_progress_event_contract() -> None:
    event = build_progress_event(
        run_id=uuid4(),
        routebook_id=uuid4(),
        stage=WorkflowStage.SAVING_VERSION,
        status=WorkflowStatus.RUNNING,
        message="saving",
        completed=1,
        total=2,
    )

    assert event.progress.completed == 1
    assert event.stage == WorkflowStage.SAVING_VERSION
