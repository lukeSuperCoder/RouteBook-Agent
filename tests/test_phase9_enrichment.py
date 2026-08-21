from uuid import uuid4

import httpx

from services.api.app.enrichment import (
    EnrichmentBudget,
    PlaceEnrichmentService,
    PlaceSearchMaterial,
    SearchRequest,
    SearchResponse,
    SearchResult,
    ZhipuWebSearchPrimeProvider,
)
from services.api.app.enums import FactStatus
from services.api.app.providers.cache import InMemoryProviderCache
from services.api.app.schemas import PlaceSnapshot


class StubSearchProvider:
    def __init__(self, response: SearchResponse) -> None:
        self.response = response
        self.calls = 0

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls += 1
        return self.response


class StubBatchSummarizer:
    def __init__(self) -> None:
        self.calls = 0
        self.material_count = 0

    def summarize_all(
        self, *, city: str, materials: list[PlaceSearchMaterial]
    ) -> dict:
        self.calls += 1
        self.material_count = len(materials)
        return {item.place_id: f"{item.place_name}的批量攻略摘要" for item in materials}


def place(name: str = "故宫博物院") -> PlaceSnapshot:
    return PlaceSnapshot(
        id=uuid4(),
        provider="amap",
        provider_place_id="poi-1",
        name=name,
        district="东城区",
        longitude=116.397,
        latitude=39.916,
        category_normalized="博物馆",
        semantic_type="attraction",
        status=FactStatus.VERIFIED,
    )


def test_provider_maps_mcp_content_and_never_exposes_key_in_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        assert b"secret" not in request.content
        payload = __import__("json").loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "test-session"},
                json={"result": {"protocolVersion": "2025-03-26"}},
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        assert request.headers["mcp-session-id"] == "test-session"
        assert payload["params"]["name"] == "web_search_prime"
        return httpx.Response(
            200,
            json={
                "result": {
                    "content": [
                        {
                            "text": '{"results":[{"title":"故宫博物院参观须知",'
                            '"url":"https://www.dpm.org.cn/visit.html",'
                            '"snippet":"开放时间 08:30-17:00"}]}'
                        }
                    ]
                }
            },
        )

    provider = ZhipuWebSearchPrimeProvider(
        endpoint="https://example.test/mcp",
        api_key="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    result = provider.search(
        SearchRequest(
            query="北京 故宫博物院 官方 开放时间",
            request_id="request-1",
            routebook_id=uuid4(),
            place_id=uuid4(),
        )
    )
    assert result.results[0].rank == 1
    assert result.results[0].site_name == "www.dpm.org.cn"


def test_budget_priority_cache_and_fact_extraction() -> None:
    target = place()
    provider = StubSearchProvider(
        SearchResponse(
            results=[
                SearchResult(
                    title="故宫博物院参观须知",
                    url="https://www.dpm.org.cn/visit.html",
                    snippet="北京东城区故宫博物院开放时间 08:30-17:00，需提前预约。",
                    site_name="故宫博物院",
                    rank=1,
                )
            ]
        )
    )
    cache = InMemoryProviderCache()
    service = PlaceEnrichmentService(provider=provider, cache=cache)
    first = service.enrich(
        routebook_id=uuid4(),
        city="北京",
        places=[
            place("酒店").model_copy(
                update={"category_normalized": "酒店", "semantic_type": "hotel"}
            ),
            target,
        ],
        request_id="request-1",
        budget=EnrichmentBudget(max_search_requests=1),
    )
    second = service.enrich(
        routebook_id=uuid4(),
        city="北京",
        places=[target],
        request_id="request-2",
        budget=EnrichmentBudget(max_search_requests=1),
    )
    assert provider.calls == 1
    assert first == second
    assert first[0].status == FactStatus.UNVERIFIED
    assert [item.type for item in first[0].highlights] == ["opening_hours", "reservation"]


def test_search_results_are_summarized_without_truth_verification_gate() -> None:
    target = place()
    provider = StubSearchProvider(
        SearchResponse(
            results=[
                SearchResult(
                    title="故宫博物院官网",
                    url="https://a.gov.cn",
                    snippet="北京东城区故宫博物院开放时间 08:30-17:00",
                    site_name="故宫博物院",
                    rank=1,
                ),
                SearchResult(
                    title="故宫博物院公告",
                    url="https://b.gov.cn",
                    snippet="北京东城区故宫博物院开放时间 09:00-16:00",
                    site_name="故宫博物院",
                    rank=2,
                ),
            ]
        )
    )
    result = PlaceEnrichmentService(provider=provider).enrich(
        routebook_id=uuid4(),
        city="北京",
        places=[target],
        request_id="request",
        budget=EnrichmentBudget(),
    )
    assert result[0].summary
    assert result[0].status == FactStatus.UNVERIFIED


def test_falls_back_to_search_snippet_when_no_actionable_keywords_exist() -> None:
    target = place("怪楼奇园")
    provider = StubSearchProvider(
        SearchResponse(
            results=[
                SearchResult(
                    title="北戴河怪楼奇园游玩参考",
                    url="https://example.com/place",
                    snippet="园区以奇特建筑和灯光景观为特色，适合傍晚散步拍照。",
                    rank=1,
                )
            ]
        )
    )
    result = PlaceEnrichmentService(provider=provider).enrich(
        routebook_id=uuid4(),
        city="北戴河",
        places=[target],
        request_id="request",
        budget=EnrichmentBudget(),
    )
    assert result[0].summary == "园区以奇特建筑和灯光景观为特色，适合傍晚散步拍照"
    assert result[0].sources
    assert result[0].status == FactStatus.UNVERIFIED


def test_all_places_are_sent_to_llm_in_one_batch() -> None:
    provider = StubSearchProvider(
        SearchResponse(
            results=[
                SearchResult(
                    title="游玩参考",
                    url="https://example.com/place",
                    snippet="适合散步拍照。",
                    rank=1,
                )
            ]
        )
    )
    summarizer = StubBatchSummarizer()
    targets = [place("故宫博物院"), place("国家博物馆")]
    result = PlaceEnrichmentService(provider=provider, summarizer=summarizer).enrich(
        routebook_id=uuid4(),
        city="北京",
        places=targets,
        request_id="request",
        budget=EnrichmentBudget(),
    )
    assert summarizer.calls == 1
    assert summarizer.material_count == 2
    assert [item.summary for item in result] == [
        "故宫博物院的批量攻略摘要",
        "国家博物馆的批量攻略摘要",
    ]


def test_summary_keeps_actionable_information_and_drops_scenic_introduction() -> None:
    sentences = PlaceEnrichmentService._action_sentences(
        "这是一座历史悠久的皇家园林，景色壮丽。开放时间为每日06:00—21:00；"
        "建议游玩1至2小时，可登顶观景。"
    )
    assert sentences == ["开放时间为每日06:00—21:00", "建议游玩1至2小时，可登顶观景"]
    assert PlaceEnrichmentService._action_sentences(
        "08:30. 开放入馆时间. 16:00. 停止入馆时间. 17:00. 闭馆 ..."
    ) == ["开放入馆时间 08:30", "停止入馆时间 16:00", "闭馆时间 17:00"]
