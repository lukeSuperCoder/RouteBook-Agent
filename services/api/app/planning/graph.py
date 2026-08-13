from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..schemas import RequirementSnapshot
from .models import PlanningPlace, PlanningResult
from .service import ItineraryPlanningService


class ItineraryPlanningState(TypedDict):
    requirements: dict[str, Any]
    places: list[dict[str, Any]]
    result: dict[str, Any] | None


def build_itinerary_planning_subgraph(
    service: ItineraryPlanningService, checkpointer: Any | None = None
) -> Any:
    def generate(state: ItineraryPlanningState) -> dict[str, dict[str, Any]]:
        result = service.plan(
            RequirementSnapshot.model_validate(state["requirements"]),
            [PlanningPlace.model_validate(item) for item in state["places"]],
        )
        return {"result": result.model_dump(mode="json")}

    builder = StateGraph(ItineraryPlanningState)
    builder.add_node("generate_validate_repair", generate)
    builder.add_edge(START, "generate_validate_repair")
    builder.add_edge("generate_validate_repair", END)
    return builder.compile(checkpointer=checkpointer)


def invoke_itinerary_planning_subgraph(
    service: ItineraryPlanningService,
    requirements: RequirementSnapshot,
    places: list[PlanningPlace],
) -> PlanningResult:
    graph = build_itinerary_planning_subgraph(service)
    state = graph.invoke(
        {
            "requirements": requirements.model_dump(mode="json"),
            "places": [item.model_dump(mode="json") for item in places],
            "result": None,
        }
    )
    return PlanningResult.model_validate(state["result"])
