from collections.abc import Callable
import logging
from typing import Any, Literal, Protocol, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .ai import AnthropicRequirementExtractor, TravelRequirements
from .amap import AmapPlaceSearcher


log = logging.getLogger("routebook.graph")


class PlaceCandidate(TypedDict):
    id: str
    provider: str
    name: str
    address: str
    district: str
    longitude: float
    latitude: float
    coordinate_system: str
    category: str
    status: str


class RouteBookState(TypedDict):
    user_message: str
    destination: str | None
    days: int | None
    must_visit: list[str]
    suggested_visit: list[str]
    place_queue: list[str]
    current_place_name: str | None
    candidates: list[PlaceCandidate]
    confirmed_places: list[PlaceCandidate]
    confirmed_place_id: str | None
    itinerary: list[str]
    stage: str


class PlaceSearcher(Protocol):
    def search(self, keyword: str, region: str) -> list[dict[str, Any]]: ...


RequirementExtractor = Callable[[str], TravelRequirements]


def make_extract_requirements(extractor: RequirementExtractor):
    def extract_requirements(state: RouteBookState) -> dict:
        log.info("节点开始 node=extract_requirements stage=%s", state["stage"])
        requirements = extractor(state["user_message"])
        places_to_resolve = [
            *requirements.must_visit,
            *requirements.suggested_visit,
        ]
        log.info("节点完成 node=extract_requirements next=search_places")
        return {
            "destination": requirements.destination or "待确认目的地",
            "days": requirements.days or 1,
            "must_visit": requirements.must_visit,
            "suggested_visit": requirements.suggested_visit,
            "place_queue": places_to_resolve,
            "stage": "requirements_extracted",
        }

    return extract_requirements


def make_search_places(searcher: PlaceSearcher):
    def search_places(state: RouteBookState) -> dict:
        log.info(
            "节点开始 node=search_places remaining=%d confirmed=%d",
            len(state["place_queue"]),
            len(state["confirmed_places"]),
        )
        if not state["place_queue"]:
            log.info("地点队列为空 next=build_itinerary")
            return {"candidates": [], "stage": "places_resolved"}

        keyword = state["place_queue"][0]
        candidates = searcher.search(keyword, state["destination"] or "")
        update: dict[str, Any] = {
            "current_place_name": keyword,
            "candidates": candidates,
            "stage": "places_searched",
        }
        if len(candidates) == 1:
            log.info(
                "唯一候选自动确认 keyword=%s place_id=%s name=%s",
                keyword,
                candidates[0]["id"],
                candidates[0]["name"],
            )
            update.update(
                {
                    "confirmed_places": [*state["confirmed_places"], candidates[0]],
                    "confirmed_place_id": candidates[0]["id"],
                    "place_queue": state["place_queue"][1:],
                    "candidates": [],
                    "stage": "place_auto_confirmed",
                }
            )
        else:
            log.info("地点存在歧义 keyword=%s candidates=%d next=confirm_place", keyword, len(candidates))
        return update

    return search_places


def route_after_search(
    state: RouteBookState,
) -> Literal["search_places", "confirm_place", "build_itinerary"]:
    if len(state["candidates"]) > 1:
        return "confirm_place"
    if state["place_queue"]:
        return "search_places"
    return "build_itinerary"


def confirm_place(state: RouteBookState) -> dict:
    log.info(
        "暂停等待地点确认 keyword=%s candidates=%d",
        state["current_place_name"],
        len(state["candidates"]),
    )
    selected_id = interrupt(
        {
            "type": "place_disambiguation",
            "place_name": state["current_place_name"],
            "question": f"搜索到多个‘{state['current_place_name']}’，请选择一个",
            "candidates": state["candidates"],
        }
    )
    selected = next(
        (item for item in state["candidates"] if item["id"] == selected_id),
        None,
    )
    if selected is None:
        raise ValueError(f"未知地点 ID: {selected_id}")
    log.info(
        "地点确认后恢复 keyword=%s place_id=%s name=%s",
        state["current_place_name"],
        selected_id,
        selected["name"],
    )
    return {
        "confirmed_places": [*state["confirmed_places"], selected],
        "confirmed_place_id": selected_id,
        "place_queue": state["place_queue"][1:],
        "candidates": [],
        "stage": "place_confirmed",
    }


def build_itinerary(state: RouteBookState) -> dict:
    log.info(
        "节点开始 node=build_itinerary days=%s confirmed_places=%d",
        state["days"],
        len(state["confirmed_places"]),
    )
    days = state["days"] or 1
    places = [place["name"] for place in state["confirmed_places"]]
    itinerary = []
    for day in range(1, days + 1):
        assigned = places[day - 1 :: days]
        activity = "—".join(assigned) if assigned else "自由探索与休息"
        prefix = f"抵达{state['destination']}，" if day == 1 else ""
        suffix = "，适时返程" if day == days else ""
        itinerary.append(f"第 {day} 天：{prefix}{activity}{suffix}")
    log.info("节点完成 node=build_itinerary itinerary_days=%d stage=completed", len(itinerary))
    return {"itinerary": itinerary, "stage": "completed"}


def build_graph(
    requirement_extractor: RequirementExtractor | None = None,
    place_searcher: PlaceSearcher | None = None,
):
    extractor = requirement_extractor or AnthropicRequirementExtractor()
    searcher = place_searcher or AmapPlaceSearcher()
    builder = StateGraph(RouteBookState)
    builder.add_node("extract_requirements", make_extract_requirements(extractor))
    builder.add_node("search_places", make_search_places(searcher))
    builder.add_node("confirm_place", confirm_place)
    builder.add_node("build_itinerary", build_itinerary)

    builder.add_edge(START, "extract_requirements")
    builder.add_edge("extract_requirements", "search_places")
    builder.add_conditional_edges("search_places", route_after_search)
    builder.add_edge("confirm_place", "search_places")
    builder.add_edge("build_itinerary", END)
    return builder.compile(checkpointer=InMemorySaver())


def initial_state(message: str) -> RouteBookState:
    return {
        "user_message": message,
        "destination": None,
        "days": None,
        "must_visit": [],
        "suggested_visit": [],
        "place_queue": [],
        "current_place_name": None,
        "candidates": [],
        "confirmed_places": [],
        "confirmed_place_id": None,
        "itinerary": [],
        "stage": "started",
    }
