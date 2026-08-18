from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from scripts.evaluate_phase3_requirements import evaluate
from services.api.app.config import Settings
from services.api.app.enums import RequirementSource
from services.api.app.requirements import (
    ClarificationAnswer,
    ExtractionResult,
    ExtractionTrace,
    RequirementPatch,
    RequirementPatchValue,
    RequirementService,
    build_requirement_graph,
    initial_requirement_state,
)
from services.api.app.requirements.extractor import (
    AnthropicRequirementExtractor,
    RequirementExtractionError,
)
from services.api.app.schemas import RequirementSnapshot, RequirementValue

TODAY = date(2026, 8, 13)


class FakeExtractor:
    def __init__(self, patches: dict[str, RequirementPatch]) -> None:
        self.patches = patches
        self.calls: list[str] = []

    def extract(
        self,
        message: str,
        current: RequirementSnapshot,
        *,
        today: date,
    ) -> ExtractionResult:
        assert today == TODAY
        self.calls.append(message)
        patch = self.patches.get(message)
        if patch is None:
            raise RequirementExtractionError("fixture has no patch")
        return ExtractionResult(
            patch=patch,
            trace=ExtractionTrace(
                prompt_version="requirement-extraction-v1",
                model="fake-structured-model",
                response_id=f"response-{len(self.calls)}",
                attempt_count=1,
                latency_ms=1,
                recorded_at=datetime.now(UTC),
            ),
        )


def explicit[T](value: T) -> RequirementPatchValue[T]:
    return RequirementPatchValue(
        value=value,
        source=RequirementSource.EXPLICIT,
        confidence=0.99,
    )


def test_requirement_replay_evaluation_meets_frozen_threshold() -> None:
    dataset = Path("docs/evaluation/phase3-requirements.json")
    passed, total, score = evaluate(dataset)
    assert (passed, total, score) == (5, 5, 1.0)


def test_explicit_value_is_protected_from_inferred_overwrite() -> None:
    current = RequirementSnapshot(
        intensity=RequirementValue(
            value="relaxed",
            source=RequirementSource.EXPLICIT,
            confidence=1.0,
            confirmed=True,
        )
    )
    patch = RequirementPatch(
        intensity=RequirementPatchValue(
            value="compact",
            source=RequirementSource.INFERRED,
            confidence=0.8,
        )
    )

    decision = RequirementService(today=TODAY).apply(current, patch)

    assert decision.snapshot.intensity.value == "relaxed"
    assert decision.conflicts[0].reason == "inferred_cannot_override_confirmed"


def test_explicit_correction_can_replace_confirmed_value() -> None:
    current = RequirementSnapshot(
        days=RequirementValue(
            value=2,
            source=RequirementSource.EXPLICIT,
            confidence=1.0,
            confirmed=True,
        )
    )
    decision = RequirementService(today=TODAY).apply(
        current, RequirementPatch(days=explicit(4))
    )
    assert decision.snapshot.days.value == 4
    assert decision.snapshot.days.confirmed is True


def test_list_patch_can_append_and_remove_without_losing_confirmed_items() -> None:
    current = RequirementSnapshot(
        must_visit_place_texts=RequirementValue(
            value=["中山陵", "夫子庙"],
            source=RequirementSource.EXPLICIT,
            confidence=1.0,
            confirmed=True,
        )
    )
    appended = RequirementService(today=TODAY).apply(
        current,
        RequirementPatch(
            must_visit_place_texts=RequirementPatchValue(
                value=["南京博物院", "中山陵"],
                source=RequirementSource.EXPLICIT,
                confidence=0.99,
                operation="append",
            )
        ),
    )
    removed = RequirementService(today=TODAY).apply(
        appended.snapshot,
        RequirementPatch(
            must_visit_place_texts=RequirementPatchValue(
                value=["夫子庙"],
                source=RequirementSource.EXPLICIT,
                confidence=0.99,
                operation="remove",
            )
        ),
    )

    assert appended.snapshot.must_visit_place_texts.value == [
        "中山陵",
        "夫子庙",
        "南京博物院",
    ]
    assert removed.snapshot.must_visit_place_texts.value == ["中山陵", "南京博物院"]


def test_clarification_is_limited_to_three_blocking_questions() -> None:
    decision = RequirementService(today=TODAY).apply(
        RequirementSnapshot(), RequirementPatch()
    )
    assert len(decision.blocking_issues) == 5
    assert len(decision.questions) == 3
    assert [question.issue_code for question in decision.questions] == [
        "missing_trip_scope",
        "missing_start_date",
        "missing_days",
    ]
    assert decision.questions[0].input_type == "single_choice"
    assert len(decision.questions[0].options) == 2


def test_destination_only_month_plan_does_not_require_origin_or_exact_date() -> None:
    decision = RequirementService(today=TODAY).apply(
        RequirementSnapshot(),
        RequirementPatch(
            trip_scope=explicit("destination_only"),
            destination=explicit("北京"),
            date_precision=explicit("month_only"),
            travel_month=explicit(9),
            days=explicit(3),
            transport_mode=explicit("public_transit"),
        ),
    )

    assert decision.ready is True
    assert decision.snapshot.origin.value is None
    assert decision.snapshot.start_date.value is None
    assert decision.snapshot.trip_scope.decision_status == "confirmed"
    assert decision.snapshot.intensity.decision_status == "suggested"


def test_flexible_date_question_can_be_skipped_explicitly() -> None:
    decision = RequirementService(today=TODAY).apply(
        RequirementSnapshot(),
        RequirementPatch(
            trip_scope=explicit("destination_only"),
            destination=explicit("北京"),
            days=explicit(3),
            transport_mode=explicit("system_decides"),
        ),
    )

    date_question = next(item for item in decision.questions if item.issue_code == "missing_start_date")
    assert date_question.input_type == "date"
    assert date_question.allow_skip is True
    assert date_question.skip_label == "日期暂未确定"


def test_requirement_graph_interrupts_then_resumes_from_same_thread() -> None:
    first = "去南京玩三天"
    answer = "从上海出发，9月1日开始，自驾"
    extractor = FakeExtractor(
        {
            first: RequirementPatch(destination=explicit("南京"), days=explicit(3)),
            answer: RequirementPatch(
                origin=explicit("上海"),
                start_date=explicit(date(2026, 9, 1)),
                transport_mode=explicit("driving"),
            ),
        }
    )
    graph = build_requirement_graph(
        extractor=extractor,
        service=RequirementService(today=TODAY),
        checkpointer=MemorySaver(),
        today=TODAY,
    )
    config = {"configurable": {"thread_id": "phase3-resume"}}

    interrupted = graph.invoke(
        initial_requirement_state(
            workflow_run_id="run-001",
            routebook_id="routebook-001",
            message_id="message-001",
            user_message=first,
        ),
        config=config,
    )
    assert interrupted["job_stage"] == "waiting_for_clarification"
    assert len(interrupted["__interrupt__"][0].value["questions"]) == 3

    completed = graph.invoke(
        Command(
            resume=ClarificationAnswer(
                message_id="message-002", text=answer
            ).model_dump(mode="json")
        ),
        config=config,
    )

    assert completed["job_stage"] == "requirements_ready"
    assert completed["requirements"]["origin"]["value"] == "上海"
    assert completed["requirements"]["intensity"]["source"] == "default"
    assert completed["processed_message_ids"] == ["message-001", "message-002"]
    assert extractor.calls == [first, answer]


def test_duplicate_resume_message_is_idempotent() -> None:
    first = "去南京玩"
    answer = "从上海出发"
    extractor = FakeExtractor(
        {
            first: RequirementPatch(destination=explicit("南京")),
            answer: RequirementPatch(origin=explicit("上海")),
        }
    )
    graph = build_requirement_graph(
        extractor=extractor,
        service=RequirementService(today=TODAY),
        checkpointer=MemorySaver(),
        today=TODAY,
    )
    config = {"configurable": {"thread_id": "phase3-idempotent-resume"}}
    graph.invoke(
        initial_requirement_state(
            workflow_run_id="run-002",
            routebook_id="routebook-002",
            message_id="message-101",
            user_message=first,
        ),
        config=config,
    )
    payload: dict[str, Any] = ClarificationAnswer(
        message_id="message-102", text=answer
    ).model_dump(mode="json")
    once = graph.invoke(Command(resume=payload), config=config)
    assert once["job_stage"] == "waiting_for_clarification"
    twice = graph.invoke(Command(resume=payload), config=config)

    assert twice["requirements"]["origin"]["value"] == "上海"
    assert twice["processed_message_ids"] == ["message-101", "message-102"]
    assert extractor.calls == [first, answer]


def test_extraction_failure_degrades_to_clarification() -> None:
    extractor = FakeExtractor({})
    graph = build_requirement_graph(
        extractor=extractor,
        service=RequirementService(today=TODAY),
        checkpointer=MemorySaver(),
        today=TODAY,
    )
    result = graph.invoke(
        initial_requirement_state(
            workflow_run_id="run-003",
            routebook_id="routebook-003",
            message_id="message-201",
            user_message="这句话无法解析",
        ),
        config={"configurable": {"thread_id": "phase3-fallback"}},
    )

    assert result["job_stage"] == "waiting_for_clarification"
    assert result["warnings"][0]["code"] == "REQUIREMENT_EXTRACTION_FAILED"
    questions = result["__interrupt__"][0].value["questions"]
    assert questions[0]["issue_code"] == "extraction_failed"
    assert len(questions) == 3


def test_anthropic_extractor_falls_back_to_strict_tool_call() -> None:
    class FakeMessages:
        def __init__(self) -> None:
            self.parse_calls = 0
            self.create_calls = 0

        def parse(self, **_kwargs):
            self.parse_calls += 1
            raise ValueError("compatible endpoint returned fenced JSON")

        def create(self, **kwargs):
            self.create_calls += 1
            assert kwargs["tool_choice"] == {
                "type": "tool",
                "name": "extract_requirement_patch",
            }
            return SimpleNamespace(
                id="tool-response-001",
                usage=SimpleNamespace(input_tokens=100, output_tokens=50),
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="extract_requirement_patch",
                        input={
                            "destination": {
                                "value": "南京",
                                "source": "explicit",
                                "confidence": 0.99,
                            }
                        },
                    )
                ],
            )

    messages = FakeMessages()
    client = SimpleNamespace(messages=messages)
    extractor = AnthropicRequirementExtractor(
        Settings(
            _env_file=None,
            anthropic_api_key="test-key",
            model_id="compatible-model",
            requirement_max_attempts=2,
        ),
        client=client,
    )

    result = extractor.extract(
        "去南京",
        RequirementSnapshot(),
        today=TODAY,
    )

    assert result.patch.destination is not None
    assert result.patch.destination.value == "南京"
    assert result.trace.attempt_count == 2
    assert result.trace.response_id == "tool-response-001"
    assert messages.parse_calls == 1
    assert messages.create_calls == 1
