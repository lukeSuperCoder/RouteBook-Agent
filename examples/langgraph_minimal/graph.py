from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class PlaceCandidate(TypedDict):
    id: str
    name: str
    address: str


class RouteBookState(TypedDict):
    user_message: str
    destination: str | None
    days: int | None
    candidates: list[PlaceCandidate]
    confirmed_place_id: str | None
    itinerary: list[str]
    stage: str


def extract_requirements(state: RouteBookState) -> dict:
    """Simulate structured LLM extraction for the first prototype."""
    message = state["user_message"]
    destination = "南京" if "南京" in message else "待确认目的地"
    days = 3 if "三天" in message or "3天" in message else 1
    return {
        "destination": destination,
        "days": days,
        "stage": "requirements_extracted",
    }


def search_places(state: RouteBookState) -> dict:
    """Simulate an Amap search that returns two places named 鼓楼."""
    candidates: list[PlaceCandidate] = []
    if "鼓楼" in state["user_message"]:
        candidates = [
            {
                "id": "gulou_square",
                "name": "鼓楼广场",
                "address": "南京市鼓楼区北京西路",
            },
            {
                "id": "gulou_park",
                "name": "鼓楼公园",
                "address": "南京市鼓楼区北京西路1-1号",
            },
        ]
    return {"candidates": candidates, "stage": "places_searched"}


def route_after_search(
    state: RouteBookState,
) -> Literal["confirm_place", "build_itinerary"]:
    if len(state["candidates"]) > 1:
        return "confirm_place"
    return "build_itinerary"


def confirm_place(state: RouteBookState) -> dict:
    selected_id = interrupt(
        {
            "type": "place_disambiguation",
            "question": "搜索到多个‘鼓楼’，请选择一个",
            "candidates": state["candidates"],
        }
    )
    valid_ids = {candidate["id"] for candidate in state["candidates"]}
    if selected_id not in valid_ids:
        raise ValueError(f"未知地点 ID: {selected_id}")
    return {"confirmed_place_id": selected_id, "stage": "place_confirmed"}


def build_itinerary(state: RouteBookState) -> dict:
    place_by_id = {
        candidate["id"]: candidate["name"] for candidate in state["candidates"]
    }
    selected_name = place_by_id.get(state["confirmed_place_id"], "市中心")
    days = state["days"] or 1
    templates = [
        f"第 1 天：抵达{state['destination']}，游览夫子庙与秦淮河",
        f"第 2 天：中山陵—明孝陵—{selected_name}",
        "第 3 天：玄武湖散步，返程",
    ]
    return {
        "itinerary": templates[:days],
        "stage": "completed",
    }


def build_graph():
    builder = StateGraph(RouteBookState)
    builder.add_node("extract_requirements", extract_requirements)
    builder.add_node("search_places", search_places)
    builder.add_node("confirm_place", confirm_place)
    builder.add_node("build_itinerary", build_itinerary)

    builder.add_edge(START, "extract_requirements")
    builder.add_edge("extract_requirements", "search_places")
    builder.add_conditional_edges("search_places", route_after_search)
    builder.add_edge("confirm_place", "build_itinerary")
    builder.add_edge("build_itinerary", END)
    return builder.compile(checkpointer=InMemorySaver())


def initial_state(message: str) -> RouteBookState:
    return {
        "user_message": message,
        "destination": None,
        "days": None,
        "candidates": [],
        "confirmed_place_id": None,
        "itinerary": [],
        "stage": "started",
    }
