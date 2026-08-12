import httpx
import pytest
from langgraph.types import Command

from examples.langgraph_minimal.ai import TravelRequirements, parse_tool_input
from examples.langgraph_minimal.amap import (
    AmapAuthError,
    AmapPlaceSearcher,
    AmapRateLimitError,
    PlaceNotFoundError,
    parse_place_response,
)
from examples.langgraph_minimal.graph import build_graph, initial_state


def candidate(place_id: str, name: str) -> dict:
    return {
        "id": place_id,
        "provider": "amap",
        "name": name,
        "address": "北京西路1号",
        "district": "鼓楼区",
        "longitude": 118.77,
        "latitude": 32.06,
        "coordinate_system": "GCJ-02",
        "category": "风景名胜",
        "status": "verified",
    }


class FakePlaceSearcher:
    def search(self, keyword: str, region: str) -> list[dict]:
        assert region == "南京"
        if keyword == "中山陵":
            return [candidate("zhongshan", "中山陵")]
        return [
            candidate("gulou_square", "鼓楼广场"),
            candidate("gulou_park", "鼓楼公园"),
        ]


def test_graph_auto_confirms_unique_place_then_interrupts_and_resumes() -> None:
    def fake_extractor(_: str) -> TravelRequirements:
        return TravelRequirements("南京", 3, ["中山陵", "鼓楼"])

    graph = build_graph(
        requirement_extractor=fake_extractor,
        place_searcher=FakePlaceSearcher(),
    )
    config = {"configurable": {"thread_id": "test-place-confirmation"}}

    interrupted = graph.invoke(initial_state("去南京三天"), config=config)

    assert interrupted["stage"] == "places_searched"
    assert interrupted["confirmed_places"][0]["name"] == "中山陵"
    assert interrupted["__interrupt__"][0].value["place_name"] == "鼓楼"

    completed = graph.invoke(Command(resume="gulou_park"), config=config)

    assert completed["stage"] == "completed"
    assert [place["name"] for place in completed["confirmed_places"]] == [
        "中山陵",
        "鼓楼公园",
    ]
    assert any("鼓楼公园" in day for day in completed["itinerary"])


def test_amap_search_sends_city_limit_and_normalizes_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/place/text"
        assert request.url.params["keywords"] == "西湖"
        assert request.url.params["region"] == "杭州"
        assert request.url.params["city_limit"] == "true"
        assert request.url.params["key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "infocode": "10000",
                "pois": [
                    {
                        "id": "B0FFG9",
                        "name": "西湖",
                        "address": "龙井路1号",
                        "adname": "西湖区",
                        "location": "120.130203,30.259324",
                        "type": "风景名胜",
                    }
                ],
            },
        )

    searcher = AmapPlaceSearcher(
        api_key="test-key",
        base_url="https://amap.test",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_request_interval=0,
    )

    places = searcher.search("西湖", "杭州")

    assert places[0]["provider"] == "amap"
    assert places[0]["coordinate_system"] == "GCJ-02"
    assert places[0]["longitude"] == 120.130203


def test_exact_name_results_take_priority() -> None:
    result = parse_place_response(
        {
            "status": "1",
            "infocode": "10000",
            "pois": [
                {
                    "id": "exact",
                    "name": "西湖",
                    "address": "杭州",
                    "adname": "西湖区",
                    "location": "120.1,30.2",
                    "type": "风景名胜",
                },
                {
                    "id": "other",
                    "name": "西湖文化广场",
                    "address": "杭州",
                    "adname": "拱墅区",
                    "location": "120.2,30.3",
                    "type": "商务住宅",
                },
            ],
        },
        "西湖",
    )

    assert [place.provider_place_id for place in result] == ["exact"]


def test_amap_auth_error_is_normalized() -> None:
    with pytest.raises(AmapAuthError, match="10001"):
        parse_place_response(
            {"status": "0", "infocode": "10001", "info": "INVALID_USER_KEY"},
            "西湖",
        )


def test_amap_rate_limit_error_is_normalized() -> None:
    with pytest.raises(AmapRateLimitError, match="10021"):
        parse_place_response(
            {
                "status": "0",
                "infocode": "10021",
                "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT",
            },
            "天坛公园",
        )


def test_empty_amap_result_is_normalized() -> None:
    with pytest.raises(PlaceNotFoundError, match="西湖"):
        parse_place_response(
            {"status": "1", "infocode": "10000", "pois": []},
            "西湖",
        )


def test_parse_tool_input() -> None:
    result = parse_tool_input(
        {"destination": " 杭州 ", "days": 2, "must_visit": [" 西湖 ", "灵隐寺"]}
    )
    assert result == TravelRequirements("杭州", 2, ["西湖", "灵隐寺"])


def test_parse_tool_input_deduplicates_suggestions() -> None:
    result = parse_tool_input(
        {
            "destination": "北京",
            "days": 3,
            "must_visit": ["故宫博物院"],
            "suggested_visit": ["故宫博物院", "颐和园", "天坛公园"],
        }
    )

    assert result.suggested_visit == ["颐和园", "天坛公园"]


def test_suggestions_are_limited_by_trip_days() -> None:
    result = parse_tool_input(
        {
            "destination": "北京",
            "days": 3,
            "must_visit": [],
            "suggested_visit": ["故宫", "天坛", "颐和园", "长城", "北海公园"],
        }
    )

    assert result.suggested_visit == ["故宫", "天坛", "颐和园"]
