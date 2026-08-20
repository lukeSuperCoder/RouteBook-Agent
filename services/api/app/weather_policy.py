from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .providers.models import DailyForecast

INDOOR_CATEGORIES = {"museum", "dining", "shopping", "religious"}
OUTDOOR_CATEGORIES = {"park", "attraction", "landmark"}
ADVERSE_TERMS = ("雨", "雪", "雷", "暴", "冰雹", "沙尘", "雾")
FAIR_TERMS = ("晴", "少云")


@dataclass(frozen=True)
class WeatherPreference:
    kind: str
    summary: str
    preferred_categories: tuple[str, ...]


def classify_forecast(forecast: DailyForecast) -> WeatherPreference:
    text = f"{forecast.text_day}{forecast.text_night}"
    if any(term in text for term in ADVERSE_TERMS):
        return WeatherPreference(
            kind="indoor",
            summary=f"{forecast.text_day}，优先安排室内活动",
            preferred_categories=tuple(sorted(INDOOR_CATEGORIES)),
        )
    if any(term in text for term in FAIR_TERMS):
        return WeatherPreference(
            kind="outdoor",
            summary=f"{forecast.text_day}，适合安排户外活动",
            preferred_categories=tuple(sorted(OUTDOOR_CATEGORIES)),
        )
    return WeatherPreference(
        kind="balanced",
        summary=f"{forecast.text_day}，室内外活动均衡安排",
        preferred_categories=(),
    )


def forecast_for_date(
    forecasts: Iterable[DailyForecast], target_date: date | None
) -> DailyForecast | None:
    if target_date is None:
        return None
    return next((item for item in forecasts if item.forecast_date == target_date), None)
