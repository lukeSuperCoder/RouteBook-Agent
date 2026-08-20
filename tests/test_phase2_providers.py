from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import redis
from pydantic import ValidationError

from services.api.app.config import Settings
from services.api.app.enums import FactStatus
from services.api.app.errors import (
    PlaceAmbiguousError,
    PlaceNotFoundError,
    ProviderAuthFailedError,
    ProviderBadResponseError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    RouteNotFoundError,
)
from services.api.app.providers.amap import AmapAdapter
from services.api.app.providers.cache import (
    CacheLookup,
    InMemoryProviderCache,
    RedisProviderCache,
    build_provider_cache,
    provider_cache_key,
)
from services.api.app.providers.models import Coordinate, PlaceCandidate, PlaceFact
from services.api.app.providers.place_service import PlaceFactService
from services.api.app.providers.poi_quality import (
    AdoptionAction,
    PoiScoringConfig,
    classify_semantic_type,
    normalize_category,
    score_candidates,
)
from services.api.app.providers.qweather import QWeatherAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "amap_api_key": "amap-test-secret",
        "qweather_api_key": "weather-test-secret",
        "qweather_api_host": "weather.test",
        "provider_max_attempts": 1,
        "provider_retry_backoff_seconds": 0,
        "provider_cache_enabled": False,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://provider.test")


def test_provider_cache_is_enabled_by_default_and_can_be_disabled() -> None:
    enabled = _settings(provider_cache_enabled=True)
    disabled = _settings(provider_cache_enabled=False)
    assert isinstance(build_provider_cache(enabled), RedisProviderCache)
    assert build_provider_cache(disabled) is None


def test_fact_models_reject_non_gcj02_coordinates_and_naive_times() -> None:
    with pytest.raises(ValidationError):
        Coordinate(longitude=114.3, latitude=30.5, coordinate_system="WGS-84")  # type: ignore[arg-type]

    candidate = _candidate(
        {
            "id": "A1",
            "name": "黄鹤楼",
            "type": "风景名胜;国家级景点",
            "location": "114.302467,30.544649",
        }
    )
    with pytest.raises(ValidationError):
        PlaceCandidate.model_validate(
            {**candidate.model_dump(mode="json"), "fetched_at": "2026-08-12T12:00:00"}
        )


def test_provider_cache_key_is_canonical_and_contains_no_raw_address() -> None:
    first = provider_cache_key(
        "routebook:provider:v1",
        "amap",
        "geocode",
        {"address": "武汉市武昌区黄鹤楼", "city": "武汉"},
    )
    second = provider_cache_key(
        "routebook:provider:v1",
        "amap",
        "geocode",
        {"city": "武汉", "address": "武汉市武昌区黄鹤楼"},
    )
    assert first == second
    assert "黄鹤楼" not in first


def test_redis_cache_failure_is_non_blocking() -> None:
    class BrokenRedis:
        def get(self, key: str) -> None:
            raise redis.RedisError("unavailable")

        def setex(self, key: str, ttl: int, value: str) -> None:
            raise redis.RedisError("unavailable")

    cache = RedisProviderCache("redis://localhost:6379/15")
    cache._client = BrokenRedis()  # type: ignore[assignment]
    assert cache.get("cache-key") is None
    cache.set("cache-key", {"ok": True}, ttl_seconds=10, stale_ttl_seconds=10)


def test_redis_cache_roundtrip_shape_with_fake_client() -> None:
    values: dict[str, str] = {}

    class FakeRedis:
        def get(self, key: str) -> str | None:
            return values.get(key)

        def setex(self, key: str, ttl: int, value: str) -> None:
            assert ttl == 20
            values[key] = value

    cache = RedisProviderCache("redis://localhost:6379/15")
    cache._client = FakeRedis()  # type: ignore[assignment]
    cache.set("cache-key", {"ok": True}, ttl_seconds=10, stale_ttl_seconds=10)
    lookup = cache.get("cache-key")
    assert lookup == CacheLookup(payload={"ok": True}, is_stale=False)


def test_amap_normalizes_poi_and_quality_excludes_affiliates() -> None:
    adapter = AmapAdapter(
        settings=_settings(),
        client=_client(lambda request: httpx.Response(200, json=_fixture("amap_poi.json"))),
    )

    candidates = adapter.search_places("八达岭长城", region="北京市")
    decision = score_candidates("八达岭长城", candidates, region="北京市")

    assert decision.action == AdoptionAction.AUTO_ADOPT
    assert decision.selected_provider_place_id == "A1"
    assert {item.semantic_type.value for item in candidates[1:]} == {
        "entrance",
        "transit",
        "service",
    }
    assert all(item.hard_filtered for item in decision.ranked if item.provider_place_id != "A1")

    place_id = uuid4()
    fact = PlaceFact.from_candidate(candidates[0], place_id=place_id)
    assert fact.id == place_id
    assert fact.provider_place_id == "A1"
    assert fact.category_raw and fact.category_normalized.value == "attraction"
    assert fact.coordinate.coordinate_system == "GCJ-02"

    stricter = score_candidates(
        "八达岭长城",
        candidates[:1],
        config=PoiScoringConfig(auto_adopt_threshold=0.9),
    )
    assert stricter.action == AdoptionAction.NEEDS_CONFIRMATION

    wrong_region = score_candidates("八达岭长城", candidates[:1], region="上海市")
    assert wrong_region.action == AdoptionAction.NEEDS_CONFIRMATION
    assert "region_mismatch" in wrong_region.ranked[0].evidence


def test_place_fact_service_enforces_quality_gate() -> None:
    adapter = AmapAdapter(
        settings=_settings(),
        client=_client(lambda request: httpx.Response(200, json=_fixture("amap_poi.json"))),
    )
    service = PlaceFactService(adapter)
    selected = service.require_auto_adoptable("八达岭长城", region="北京市")
    assert selected.provider_place_id == "A1"

    with pytest.raises(PlaceAmbiguousError) as exc_info:
        service.require_auto_adoptable("八达岭长城", region="")
    assert exc_info.value.details["candidate_ids"] == ["A1"]
    assert exc_info.value.details["candidates"][0]["name"] == "八达岭长城"
    assert exc_info.value.details["candidates"][0]["coordinate"]["coordinate_system"] == "GCJ-02"


def test_amap_geocode_and_both_route_modes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/geocode/geo"):
            return httpx.Response(200, json=_fixture("amap_geocode.json"))
        if request.url.path.endswith("/driving"):
            return httpx.Response(200, json=_fixture("amap_driving.json"))
        return httpx.Response(200, json=_fixture("amap_walking.json"))

    adapter = AmapAdapter(settings=_settings(), client=_client(handler))
    place = adapter.geocode("黄鹤楼", city="武汉")
    destination = Coordinate(longitude=114.305, latitude=30.548)

    driving = adapter.driving_route(place.coordinate, destination)
    walking = adapter.walking_route(place.coordinate, destination)

    assert place.match_level == "兴趣点"
    assert driving.distance_meters == 9484
    assert driving.duration_seconds == 1224
    assert driving.traffic_lights == 15
    assert walking.mode == "walking"
    assert walking.distance_meters == 860
    assert walking.tolls_yuan is None


def test_amap_maps_empty_auth_rate_limit_and_bad_responses() -> None:
    payloads = [
        ({"status": "1", "infocode": "10000", "pois": []}, PlaceNotFoundError),
        ({"status": "0", "infocode": "10001", "info": "INVALID_USER_KEY"}, ProviderAuthFailedError),
        ({"status": "0", "infocode": "10004", "info": "TOO_FAST"}, ProviderRateLimitedError),
        ({"status": "1", "infocode": "10000", "pois": {}}, ProviderBadResponseError),
    ]
    for payload, error_type in payloads:
        adapter = AmapAdapter(
            settings=_settings(),
            client=_client(lambda request, body=payload: httpx.Response(200, json=body)),
        )
        with pytest.raises(error_type):
            adapter.search_places("不存在的地点", region="武汉")

    route_adapter = AmapAdapter(
        settings=_settings(),
        client=_client(
            lambda request: httpx.Response(
                200,
                json={
                    "status": "1",
                    "infocode": "10000",
                    "route": {"paths": []},
                },
            )
        ),
    )
    with pytest.raises(RouteNotFoundError):
        route_adapter.walking_route(
            Coordinate(longitude=114.3, latitude=30.5),
            Coordinate(longitude=114.31, latitude=30.51),
        )


def test_provider_business_rate_limit_is_retried_with_a_bound() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(
                200, json={"status": "0", "infocode": "10004", "info": "TOO_FAST"}
            )
        return httpx.Response(200, json=_fixture("amap_poi.json"))

    adapter = AmapAdapter(
        settings=_settings(provider_max_attempts=3),
        client=_client(handler),
    )
    assert adapter.search_places("八达岭长城", region="北京市")[0].provider_place_id == "A1"
    assert calls == 3


def test_qweather_parses_daily_hourly_and_warning_contracts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-QW-Api-Key"] == "weather-test-secret"
        if request.url.path.endswith("/weather/3d"):
            return httpx.Response(200, json=_fixture("qweather_daily.json"))
        if request.url.path.endswith("/weather/24h"):
            return httpx.Response(200, json=_fixture("qweather_hourly.json"))
        return httpx.Response(200, json=_fixture("qweather_warning.json"))

    adapter = QWeatherAdapter(settings=_settings(), client=_client(handler))
    location = Coordinate(longitude=114.302467, latitude=30.544649)

    daily = adapter.daily_forecast(location)
    hourly = adapter.hourly_forecast(location)
    warnings = adapter.warnings(location)

    assert daily.status == FactStatus.VERIFIED
    assert len(daily.items) == 3 and daily.items[0].temp_max_c == 36
    assert len(hourly.items) == 2 and hourly.items[1].precipitation_mm == 0.2
    assert warnings.items[0].title == "高温黄色预警"


def test_qweather_empty_warning_is_success_and_auth_is_not_retried() -> None:
    calls = 0

    def no_warning(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": "200", "updateTime": "2026-08-12T14:33+08:00", "warning": []}
        )

    adapter = QWeatherAdapter(settings=_settings(), client=_client(no_warning))
    result = adapter.warnings(Coordinate(longitude=114.3, latitude=30.5))
    assert result.status == FactStatus.VERIFIED
    assert result.items == []

    def unauthorized(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": {"title": "Unauthorized"}})

    adapter = QWeatherAdapter(
        settings=_settings(provider_max_attempts=3), client=_client(unauthorized)
    )
    with pytest.raises(ProviderAuthFailedError):
        adapter.daily_forecast(Coordinate(longitude=114.3, latitude=30.5))
    assert calls == 1


def test_qweather_missing_business_code_is_bad_response() -> None:
    adapter = QWeatherAdapter(
        settings=_settings(),
        client=_client(lambda request: httpx.Response(200, json={})),
    )
    with pytest.raises(ProviderBadResponseError):
        adapter.daily_forecast(Coordinate(longitude=114.3, latitude=30.5))

    rate_limited = QWeatherAdapter(
        settings=_settings(),
        client=_client(lambda request: httpx.Response(200, json={"code": "429"})),
    )
    with pytest.raises(ProviderRateLimitedError):
        rate_limited.hourly_forecast(Coordinate(longitude=114.3, latitude=30.5))


def test_http_timeout_is_bounded_and_auth_failure_does_not_retry() -> None:
    timeout_calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal timeout_calls
        timeout_calls += 1
        raise httpx.ReadTimeout("timeout", request=request)

    adapter = AmapAdapter(
        settings=_settings(provider_max_attempts=2),
        client=_client(timeout),
    )
    with pytest.raises(ProviderUnavailableError):
        adapter.geocode("黄鹤楼", city="武汉")
    assert timeout_calls == 2


def test_amap_auth_is_injected_without_entering_business_params() -> None:
    seen_query: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_query.update(dict(request.url.params))
        return httpx.Response(200, json=_fixture("amap_geocode.json"))

    adapter = AmapAdapter(settings=_settings(), client=_client(handler))
    adapter.geocode("黄鹤楼", city="武汉")
    assert seen_query["key"] == "amap-test-secret"
    assert seen_query["address"] == "黄鹤楼"


def test_stale_cache_is_returned_after_timeout() -> None:
    now = 1_000.0

    def clock() -> float:
        return now

    cache = InMemoryProviderCache(clock=clock)
    should_timeout = False

    def handler(request: httpx.Request) -> httpx.Response:
        if should_timeout:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json=_fixture("qweather_daily.json"))

    adapter = QWeatherAdapter(
        settings=_settings(weather_daily_cache_ttl_seconds=10, provider_stale_ttl_seconds=30),
        client=_client(handler),
        cache=cache,
    )
    location = Coordinate(longitude=114.3, latitude=30.5)
    assert adapter.daily_forecast(location).status == FactStatus.VERIFIED

    now += 11
    should_timeout = True
    stale = adapter.daily_forecast(location)
    assert stale.status == FactStatus.STALE
    assert stale.items[0].status == FactStatus.STALE


def test_qweather_no_data_is_explicitly_unavailable() -> None:
    adapter = QWeatherAdapter(
        settings=_settings(),
        client=_client(lambda request: httpx.Response(200, json={"code": "204"})),
    )
    result = adapter.daily_forecast(Coordinate(longitude=114.3, latitude=30.5))
    assert result.status == FactStatus.UNAVAILABLE
    assert result.items == []

    no_content = QWeatherAdapter(
        settings=_settings(),
        client=_client(lambda request: httpx.Response(204)),
    )
    result = no_content.hourly_forecast(Coordinate(longitude=114.3, latitude=30.5))
    assert result.status == FactStatus.UNAVAILABLE

    v2_no_data = QWeatherAdapter(
        settings=_settings(),
        client=_client(
            lambda request: httpx.Response(
                400,
                json={
                    "error": {
                        "type": (
                            "https://dev.qweather.com/docs/resource/error-code/#data-not-available"
                        ),
                        "title": "Data Not Available",
                    }
                },
            )
        ),
    )
    result = v2_no_data.daily_forecast(Coordinate(longitude=114.3, latitude=30.5))
    assert result.status == FactStatus.UNAVAILABLE

    invalid_parameter = QWeatherAdapter(
        settings=_settings(),
        client=_client(
            lambda request: httpx.Response(
                400,
                json={
                    "error": {
                        "type": (
                            "https://dev.qweather.com/docs/resource/error-code/#invalid-parameters"
                        ),
                        "title": "Invalid Parameters",
                    }
                },
            )
        ),
    )
    with pytest.raises(ProviderError) as exc_info:
        invalid_parameter.daily_forecast(Coordinate(longitude=114.3, latitude=30.5))
    assert exc_info.value.details["provider_problem"] == "invalid-parameters"


def test_network_failure_traceback_does_not_expose_query_credential() -> None:
    secret = "must-not-appear-in-traceback"

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unavailable", request=request)

    adapter = AmapAdapter(
        api_key=secret,
        settings=_settings(),
        client=_client(timeout),
    )
    try:
        adapter.geocode("黄鹤楼", city="武汉")
    except ProviderUnavailableError:
        formatted = traceback.format_exc()
    else:
        pytest.fail("expected provider failure")
    assert secret not in formatted


def test_adversarial_poi_evaluation_cases() -> None:
    evaluation = json.loads(
        (Path(__file__).parents[1] / "docs/evaluation/phase2-poi-adversarial.json").read_text(
            encoding="utf-8"
        )
    )
    fixture_places = _fixture("amap_poi.json")["pois"]
    for case in evaluation["cases"]:
        raw_candidates = fixture_places if "fixture" in case else case["candidates"]
        candidates = [_candidate(item) for item in raw_candidates]
        decision = score_candidates(case["keyword"], candidates, region=case["region"])
        assert decision.action.value == case["expected_action"], case["id"]
        if expected := case.get("expected_provider_place_id"):
            assert decision.selected_provider_place_id == expected, case["id"]


def _candidate(item: dict[str, Any]) -> PlaceCandidate:
    category = item["type"]
    longitude, latitude = item["location"].split(",")
    return PlaceCandidate(
        provider_place_id=item["id"],
        name=item["name"],
        address=item.get("address", ""),
        city=item.get("city", item.get("cityname", "")),
        district=item.get("district", item.get("adname", "")),
        adcode=item.get("adcode", ""),
        coordinate=Coordinate(longitude=float(longitude), latitude=float(latitude)),
        category_raw=category,
        category_normalized=normalize_category(category),
        semantic_type=classify_semantic_type(item["name"], category),
        fetched_at="2026-08-12T00:00:00Z",
    )
