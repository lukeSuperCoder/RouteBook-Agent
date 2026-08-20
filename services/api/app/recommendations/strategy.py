from __future__ import annotations

from ..providers.models import DailyForecast, NormalizedPlaceCategory
from ..schemas import RequirementSnapshot
from ..weather_policy import classify_forecast
from .models import DiversityConstraint, GeographicScope, RecommendationStrategy

THEME_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "历史": ("museum", "landmark", "religious"),
    "人文": ("museum", "landmark"),
    "博物馆": ("museum",),
    "自然": ("park", "attraction"),
    "亲子": ("park", "museum", "attraction"),
    "美食": ("dining",),
    "购物": ("shopping",),
}
THEME_QUERY_SUFFIXES: dict[str, tuple[str, ...]] = {
    "历史": ("历史景点", "博物馆"),
    "人文": ("人文景点", "博物馆"),
    "博物馆": ("博物馆",),
    "自然": ("自然景区", "公园"),
    "亲子": ("亲子景点",),
    "美食": ("特色美食",),
    "购物": ("特色街区",),
}


def build_recommendation_strategy(
    requirements: RequirementSnapshot,
    *,
    rejected_reasons: list[str] | None = None,
    forecasts: list[DailyForecast] | None = None,
) -> RecommendationStrategy:
    destination = requirements.destination.value
    if not destination:
        raise ValueError("confirmed destination is required for recommendations")
    themes = requirements.themes.value or []
    theme_weights = {theme: round(1 / len(themes), 4) for theme in themes} if themes else {}
    categories: list[str] = []
    query_terms: list[str] = []
    for theme in themes:
        categories.extend(THEME_CATEGORY_MAP.get(theme, ()))
        query_terms.extend(
            f"{destination}{suffix}"
            for suffix in THEME_QUERY_SUFFIXES.get(theme, ())
        )
    if not categories:
        categories = ["attraction", "museum", "park", "landmark"]
    if not query_terms:
        query_terms = [f"{destination}必游景点", f"{destination}博物馆", f"{destination}公园"]
    weather_preference = None
    weather_summary = None
    if forecasts:
        relevant = forecasts
        start = requirements.start_date.value
        days = requirements.days.value
        if start is not None and days:
            relevant = [item for item in forecasts if 0 <= (item.forecast_date - start).days < days]
        preferences = [classify_forecast(item) for item in relevant]
        indoor_count = sum(item.kind == "indoor" for item in preferences)
        outdoor_count = sum(item.kind == "outdoor" for item in preferences)
        if indoor_count or outdoor_count:
            weather_preference = "indoor" if indoor_count >= outdoor_count else "outdoor"
            if weather_preference == "indoor":
                categories = ["museum", "dining", "shopping", *categories]
                query_terms = [f"{destination}室内景点", f"{destination}博物馆", *query_terms]
            else:
                categories = ["park", "attraction", "landmark", *categories]
                query_terms = [f"{destination}户外景点", f"{destination}公园", *query_terms]
            weather_summary = "；".join(item.summary for item in preferences[:3])
        elif preferences:
            weather_preference = "balanced"
            weather_summary = "；".join(item.summary for item in preferences[:3])
    query_terms = [*(requirements.must_visit_place_texts.value or []), *query_terms]
    query_terms.extend(requirements.optional_place_texts.value or [])

    reject_too_far = "too_far" in (rejected_reasons or [])
    allow_suburban = bool(requirements.suburban_acceptance.value) and not reject_too_far
    notes = requirements.notes.value or []
    excluded_districts = [
        note.removeprefix("避开区域：")
        for note in notes
        if note.startswith("避开区域：")
    ]
    return RecommendationStrategy(
        target_categories=list(dict.fromkeys(categories)),
        theme_weights=theme_weights,
        geographic_scope=GeographicScope(
            region=destination,
            excluded_districts=excluded_districts,
            allow_suburban=allow_suburban,
        ),
        negative_categories=[
            NormalizedPlaceCategory.TRANSPORT.value,
            NormalizedPlaceCategory.TRAVEL_SERVICE.value,
            NormalizedPlaceCategory.ACCOMMODATION.value,
        ],
        negative_place_texts=(requirements.excluded_place_texts.value or [])
        + (requirements.visited_place_texts.value or []),
        query_terms=list(dict.fromkeys(query_terms))[:12],
        diversity=DiversityConstraint(
            maximum_per_category=2,
            maximum_per_district=2 if not allow_suburban else 3,
            minimum_categories=2,
        ),
        weather_summary=weather_summary,
        weather_preference=weather_preference,
    )
