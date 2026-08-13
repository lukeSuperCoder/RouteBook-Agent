from __future__ import annotations

import os

import pytest

from services.api.app.config import get_settings
from services.api.app.providers.amap import AmapAdapter
from services.api.app.providers.models import Coordinate
from services.api.app.providers.qweather import QWeatherAdapter

if os.getenv("RUN_PROVIDER_LIVE_TESTS") != "1":
    pytest.skip(
        "set RUN_PROVIDER_LIVE_TESTS=1 to run provider smoke tests",
        allow_module_level=True,
    )

pytestmark = pytest.mark.provider_live
live_settings = get_settings()


@pytest.mark.skipif(not live_settings.amap_api_key, reason="AMAP_API_KEY is not configured")
def test_amap_live_poi_geocode_driving_and_walking() -> None:
    adapter = AmapAdapter(settings=live_settings)
    place = adapter.geocode("黄鹤楼", city="武汉")
    candidates = adapter.search_places("黄鹤楼", region="武汉")
    nearby = Coordinate(longitude=114.305, latitude=30.548)

    driving = adapter.driving_route(place.coordinate, nearby)
    walking = adapter.walking_route(place.coordinate, nearby)

    assert candidates
    assert driving.distance_meters > 0
    assert driving.duration_seconds > 0
    assert walking.distance_meters > 0
    assert walking.duration_seconds > 0


@pytest.mark.skipif(
    not live_settings.qweather_api_key or not live_settings.qweather_api_host,
    reason="QWEATHER_API_KEY and QWEATHER_API_HOST are not configured",
)
def test_qweather_live_daily_hourly_and_warning() -> None:
    adapter = QWeatherAdapter(settings=live_settings)
    location = Coordinate(longitude=114.302467, latitude=30.544649)

    assert adapter.daily_forecast(location).items
    assert adapter.hourly_forecast(location).items
    assert adapter.warnings(location).status.value in {"verified", "unavailable"}
