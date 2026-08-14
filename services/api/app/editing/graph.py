from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..schemas import RouteBookSnapshotV1
from .models import EditIntent, EditPlan
from .service import EditingService


class EditingState(TypedDict):
    snapshot: dict[str, Any]
    intent: dict[str, Any]
    plan: dict[str, Any] | None


def build_editing_subgraph(service: EditingService | None = None) -> Any:
    editing = service or EditingService()

    def plan_edit(state: EditingState) -> dict[str, dict[str, Any]]:
        plan = editing.plan(
            RouteBookSnapshotV1.model_validate(state["snapshot"]),
            EditIntent.model_validate(state["intent"]),
        )
        return {"plan": plan.model_dump(mode="json")}

    builder = StateGraph(EditingState)
    builder.add_node("resolve_calculate_preview", plan_edit)
    builder.add_edge(START, "resolve_calculate_preview")
    builder.add_edge("resolve_calculate_preview", END)
    return builder.compile()


def invoke_editing_subgraph(
    snapshot: RouteBookSnapshotV1, intent: EditIntent
) -> EditPlan:
    state = build_editing_subgraph().invoke(
        {
            "snapshot": snapshot.model_dump(mode="json"),
            "intent": intent.model_dump(mode="json"),
            "plan": None,
        }
    )
    return EditPlan.model_validate(state["plan"])
