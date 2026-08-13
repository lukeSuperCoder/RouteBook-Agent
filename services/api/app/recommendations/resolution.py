from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field

from ..providers.models import PlaceCandidate
from ..providers.poi_quality import GENERIC_PLACE_TERMS, AdoptionAction, AdoptionDecision


class ResolutionAction(StrEnum):
    AUTO_ADOPT = "auto_adopt"
    CONFIRM_CANDIDATE = "confirm_candidate"
    CHOOSE_PREFERENCE = "choose_preference"
    CLARIFY_NO_RESULT = "clarify_no_result"


class PlaceResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    action: ResolutionAction
    selected_provider_place_id: str | None = None
    candidates: list[PlaceCandidate] = Field(default_factory=list)
    prompt: str | None = None


class PlaceResolutionState(TypedDict):
    query: str
    candidates: list[dict[str, Any]]
    decision: dict[str, Any]
    resolution: dict[str, Any] | None


def resolve_place(
    query: str,
    candidates: list[PlaceCandidate],
    decision: AdoptionDecision,
) -> PlaceResolution:
    if decision.action == AdoptionAction.AUTO_ADOPT:
        return PlaceResolution(
            query=query,
            action=ResolutionAction.AUTO_ADOPT,
            selected_provider_place_id=decision.selected_provider_place_id,
            candidates=candidates,
        )
    eligible_ids = {item.provider_place_id for item in decision.ranked if not item.hard_filtered}
    eligible = [item for item in candidates if item.provider_place_id in eligible_ids]
    normalized = "".join(query.lower().split()).replace("景区", "")
    if normalized in GENERIC_PLACE_TERMS:
        return PlaceResolution(
            query=query,
            action=ResolutionAction.CHOOSE_PREFERENCE,
            candidates=eligible,
            prompt=f"“{query}”是泛指概念。你偏好历史、人文、自然还是亲子体验？",
        )
    if decision.action == AdoptionAction.NO_RESULT:
        return PlaceResolution(
            query=query,
            action=ResolutionAction.CLARIFY_NO_RESULT,
            candidates=[],
            prompt=f"没有找到符合质量门禁的“{query}”。请补充城市、行政区或完整名称。",
        )
    return PlaceResolution(
        query=query,
        action=ResolutionAction.CONFIRM_CANDIDATE,
        candidates=eligible[:5],
        prompt=f"找到多个“{query}”候选，请根据名称、类型、地址和行政区选择。",
    )


def build_place_resolution_subgraph(checkpointer: Any | None = None) -> Any:
    def evaluate(state: PlaceResolutionState) -> dict[str, dict[str, Any]]:
        candidates = [PlaceCandidate.model_validate(item) for item in state["candidates"]]
        decision = AdoptionDecision.model_validate(state["decision"])
        resolution = resolve_place(state["query"], candidates, decision)
        return {"resolution": resolution.model_dump(mode="python")}

    def route(state: PlaceResolutionState) -> str:
        resolution = PlaceResolution.model_validate(state["resolution"])
        return "done" if resolution.action == ResolutionAction.AUTO_ADOPT else "confirm"

    def confirm(state: PlaceResolutionState) -> dict[str, dict[str, Any]]:
        resolution = PlaceResolution.model_validate(state["resolution"])
        answer = interrupt(
            {
                "interrupt_kind": "place_confirmation",
                "query": resolution.query,
                "action": resolution.action.value,
                "prompt": resolution.prompt,
                "candidates": [
                    {
                        "provider_place_id": item.provider_place_id,
                        "name": item.name,
                        "type": item.category_normalized.value,
                        "address": item.address,
                        "district": item.district,
                    }
                    for item in resolution.candidates
                ],
            }
        )
        selected = answer.get("provider_place_id") if isinstance(answer, dict) else None
        if selected and selected not in {
            item.provider_place_id for item in resolution.candidates
        }:
            raise ValueError("selected place is not an eligible candidate")
        return {
            "resolution": resolution.model_copy(
                update={"selected_provider_place_id": selected}
            ).model_dump(mode="python")
        }

    builder = StateGraph(PlaceResolutionState)
    builder.add_node("evaluate", evaluate)
    builder.add_node("confirm", confirm)
    builder.add_edge(START, "evaluate")
    builder.add_conditional_edges("evaluate", route, {"done": END, "confirm": "confirm"})
    builder.add_edge("confirm", END)
    return builder.compile(checkpointer=checkpointer)
