from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..schemas import RequirementSnapshot
from .extractor import RequirementExtractionError, RequirementExtractor
from .models import ClarificationAnswer, ExtractionResult, RequirementDecision, RequirementPatch
from .service import RequirementService


class RequirementGraphState(TypedDict):
    workflow_run_id: str
    routebook_id: str
    message_id: str
    user_message: str
    requirements: dict[str, Any]
    requirement_patch: dict[str, Any] | None
    requirement_decision: dict[str, Any] | None
    extraction_traces: list[dict[str, Any]]
    processed_message_ids: list[str]
    extraction_failed: bool
    job_stage: str
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]


def initial_requirement_state(
    *,
    workflow_run_id: str,
    routebook_id: str,
    message_id: str,
    user_message: str,
    requirements: RequirementSnapshot | None = None,
) -> RequirementGraphState:
    return {
        "workflow_run_id": workflow_run_id,
        "routebook_id": routebook_id,
        "message_id": message_id,
        "user_message": user_message,
        "requirements": (requirements or RequirementSnapshot()).model_dump(mode="json"),
        "requirement_patch": None,
        "requirement_decision": None,
        "extraction_traces": [],
        "processed_message_ids": [],
        "extraction_failed": False,
        "job_stage": "extracting_requirements",
        "warnings": [],
        "errors": [],
    }


def build_requirement_graph(
    *,
    extractor: RequirementExtractor,
    service: RequirementService,
    checkpointer: Any,
    today: date | None = None,
) -> Any:
    current_day = today or service.today

    def extract_patch(state: RequirementGraphState) -> dict[str, Any]:
        if state["message_id"] in state["processed_message_ids"]:
            return {"requirement_patch": RequirementPatch().model_dump(mode="json")}
        snapshot = RequirementSnapshot.model_validate(state["requirements"])
        try:
            result: ExtractionResult = extractor.extract(
                state["user_message"], snapshot, today=current_day
            )
            return {
                "requirement_patch": result.patch.model_dump(mode="json"),
                "extraction_traces": [
                    *state["extraction_traces"],
                    result.trace.model_dump(mode="json"),
                ],
                "extraction_failed": False,
            }
        except RequirementExtractionError as exc:
            return {
                "requirement_patch": RequirementPatch().model_dump(mode="json"),
                "extraction_failed": True,
                "warnings": [
                    *state["warnings"],
                    {"code": "REQUIREMENT_EXTRACTION_FAILED", "error_type": type(exc).__name__},
                ],
            }

    def merge_requirements(state: RequirementGraphState) -> dict[str, Any]:
        snapshot = RequirementSnapshot.model_validate(state["requirements"])
        patch = RequirementPatch.model_validate(state["requirement_patch"] or {})
        decision = service.apply(
            snapshot,
            patch,
            extraction_failed=state["extraction_failed"],
        )
        processed = state["processed_message_ids"]
        if state["message_id"] not in processed:
            processed = [*processed, state["message_id"]]
        return {
            "requirements": decision.snapshot.model_dump(mode="json"),
            "requirement_decision": decision.model_dump(mode="json"),
            "processed_message_ids": processed,
            "job_stage": (
                "requirements_ready" if decision.ready else "waiting_for_clarification"
            ),
        }

    def route_after_merge(state: RequirementGraphState) -> str:
        decision = RequirementDecision.model_validate(state["requirement_decision"])
        return "ready" if decision.ready else "clarify"

    def ask_clarification(state: RequirementGraphState) -> dict[str, Any]:
        decision = RequirementDecision.model_validate(state["requirement_decision"])
        answer_raw = interrupt(
            {
                "kind": "requirement_clarification",
                "questions": [q.model_dump(mode="json") for q in decision.questions],
                "requirements": decision.snapshot.model_dump(mode="json"),
            }
        )
        answer = ClarificationAnswer.model_validate(answer_raw)
        return {
            "message_id": answer.message_id,
            "user_message": answer.text,
            "requirement_patch": None,
            "requirement_decision": None,
            "extraction_failed": False,
            "job_stage": "extracting_requirements",
        }

    builder = StateGraph(RequirementGraphState)
    builder.add_node("extract_requirement_patch", extract_patch)
    builder.add_node("merge_requirements", merge_requirements)
    builder.add_node("build_clarification", ask_clarification)
    builder.add_edge(START, "extract_requirement_patch")
    builder.add_edge("extract_requirement_patch", "merge_requirements")
    builder.add_conditional_edges(
        "merge_requirements",
        route_after_merge,
        {"ready": END, "clarify": "build_clarification"},
    )
    builder.add_edge("build_clarification", "extract_requirement_patch")
    return builder.compile(checkpointer=checkpointer)
