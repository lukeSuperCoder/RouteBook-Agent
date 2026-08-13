from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
from pydantic import SecretStr, ValidationError

from ..config import Settings, get_settings
from ..enums import FactStatus
from ..errors import (
    ProviderAuthFailedError,
    ProviderBadResponseError,
    ProviderDataUnavailableError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from .cache import ProviderCache, build_provider_cache, provider_cache_key
from .http import HeaderKeyAuth, ProviderHttpClient
from .models import (
    Coordinate,
    DailyForecast,
    FactCollection,
    HourlyForecast,
    ProviderModel,
    WeatherWarning,
    with_stale_collection,
)

ModelT = TypeVar("ModelT", bound=ProviderModel)
TRANSIENT_ERRORS = (ProviderUnavailableError, ProviderRateLimitedError)


class QWeatherAdapter:
    def __init__(
        self,
        *,
        api_key: str | SecretStr | None = None,
        api_host: str | None = None,
        cache: ProviderCache | None = None,
        client: httpx.Client | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        secret = api_key or self._settings.qweather_api_key
        self._api_key = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        host = (api_host or self._settings.qweather_api_host).strip().rstrip("/")
        if not self._api_key:
            raise ValueError("QWEATHER_API_KEY is required")
        if not host:
            raise ValueError("QWEATHER_API_HOST is required")
        base_url = host if host.startswith(("https://", "http://")) else f"https://{host}"
        self._cache = cache if cache is not None else build_provider_cache(self._settings)
        self._http = ProviderHttpClient(
            provider="qweather",
            base_url=base_url,
            client=client,
            auth=HeaderKeyAuth(self._api_key, header="X-QW-Api-Key"),
            connect_timeout_seconds=self._settings.provider_connect_timeout_seconds,
            read_timeout_seconds=self._settings.provider_read_timeout_seconds,
            max_attempts=self._settings.provider_max_attempts,
            retry_backoff_seconds=self._settings.provider_retry_backoff_seconds,
        )

    def daily_forecast(self, location: Coordinate) -> FactCollection[DailyForecast]:
        operation = "weather_daily"
        key = self._key(operation, {"location": location.as_query(), "days": 3})
        cached = self._cached_collection(key, DailyForecast)
        if cached is not None and not cached[1]:
            return cached[0]
        try:
            payload = self._request("/v7/weather/3d", operation, {"location": location.as_query()})
            results = self._parse_daily(payload, location)
        except ProviderDataUnavailableError:
            results = FactCollection(status=FactStatus.UNAVAILABLE, items=[])
        except TRANSIENT_ERRORS:
            if cached is not None:
                return with_stale_collection(cached[0])
            raise
        self._store_collection(key, results, self._settings.weather_daily_cache_ttl_seconds)
        return results

    def hourly_forecast(self, location: Coordinate) -> FactCollection[HourlyForecast]:
        operation = "weather_hourly"
        key = self._key(operation, {"location": location.as_query(), "hours": 24})
        cached = self._cached_collection(key, HourlyForecast)
        if cached is not None and not cached[1]:
            return cached[0]
        try:
            payload = self._request("/v7/weather/24h", operation, {"location": location.as_query()})
            results = self._parse_hourly(payload, location)
        except ProviderDataUnavailableError:
            results = FactCollection(status=FactStatus.UNAVAILABLE, items=[])
        except TRANSIENT_ERRORS:
            if cached is not None:
                return with_stale_collection(cached[0])
            raise
        self._store_collection(key, results, self._settings.weather_hourly_cache_ttl_seconds)
        return results

    def warnings(self, location: Coordinate) -> FactCollection[WeatherWarning]:
        operation = "weather_warning"
        key = self._key(operation, {"location": location.as_query()})
        cached = self._cached_collection(key, WeatherWarning)
        if cached is not None and not cached[1]:
            return cached[0]
        try:
            payload = self._request("/v7/warning/now", operation, {"location": location.as_query()})
            results = self._parse_warnings(payload, location)
        except ProviderDataUnavailableError:
            results = FactCollection(status=FactStatus.UNAVAILABLE, items=[])
        except TRANSIENT_ERRORS:
            if cached is not None:
                return with_stale_collection(cached[0])
            raise
        self._store_collection(key, results, self._settings.weather_warning_cache_ttl_seconds)
        return results

    def _request(self, path: str, operation: str, params: dict[str, str]) -> dict[str, Any]:
        return self._http.get_json(
            path,
            operation=operation,
            params=params,
            validate=lambda payload: self._validate_response(payload, operation),
            accepted_http_statuses=frozenset({400}),
        )

    @staticmethod
    def _validate_response(payload: dict[str, Any], operation: str) -> None:
        code = _string(payload.get("code"))
        problem_type = _qweather_problem_type(payload)
        if problem_type in {"data-not-available", "no-such-location"}:
            raise ProviderDataUnavailableError(
                details={"provider": "qweather", "operation": operation}
            )
        if problem_type:
            raise ProviderError(
                details={
                    "provider": "qweather",
                    "operation": operation,
                    "provider_problem": problem_type,
                }
            )
        if not code:
            raise ProviderBadResponseError(details={"provider": "qweather", "operation": operation})
        if code in {"200", "204"}:
            return
        details = {"provider": "qweather", "operation": operation, "provider_code": code}
        if code in {"401", "403"}:
            raise ProviderAuthFailedError(details=details)
        if code in {"402", "429"}:
            raise ProviderRateLimitedError(details=details)
        if code == "500":
            raise ProviderUnavailableError(details=details)
        raise ProviderError(details=details)

    def _parse_daily(
        self, payload: dict[str, Any], location: Coordinate
    ) -> FactCollection[DailyForecast]:
        if _string(payload.get("code")) == "204":
            return FactCollection(status=FactStatus.UNAVAILABLE, items=[])
        daily = payload.get("daily")
        if not isinstance(daily, list):
            raise ProviderBadResponseError(
                details={"provider": "qweather", "operation": "weather_daily"}
            )
        updated_at = _provider_updated_at(payload, "weather_daily")
        fetched_at = datetime.now(UTC)
        results: list[DailyForecast] = []
        try:
            for item in daily:
                if not isinstance(item, dict):
                    continue
                results.append(
                    DailyForecast(
                        location=location,
                        forecast_date=datetime.fromisoformat(_string(item.get("fxDate"))).date(),
                        temp_min_c=int(_string(item.get("tempMin"))),
                        temp_max_c=int(_string(item.get("tempMax"))),
                        text_day=_string(item.get("textDay")),
                        text_night=_string(item.get("textNight")),
                        wind_scale_day=_string(item.get("windScaleDay")),
                        provider_updated_at=updated_at,
                        fetched_at=fetched_at,
                    )
                )
        except (ValueError, TypeError) as exc:
            raise ProviderBadResponseError(
                details={"provider": "qweather", "operation": "weather_daily"}
            ) from exc
        return FactCollection(
            status=FactStatus.VERIFIED if results else FactStatus.UNAVAILABLE,
            items=results,
        )

    def _parse_hourly(
        self, payload: dict[str, Any], location: Coordinate
    ) -> FactCollection[HourlyForecast]:
        if _string(payload.get("code")) == "204":
            return FactCollection(status=FactStatus.UNAVAILABLE, items=[])
        hourly = payload.get("hourly")
        if not isinstance(hourly, list):
            raise ProviderBadResponseError(
                details={"provider": "qweather", "operation": "weather_hourly"}
            )
        updated_at = _provider_updated_at(payload, "weather_hourly")
        fetched_at = datetime.now(UTC)
        results: list[HourlyForecast] = []
        try:
            for item in hourly:
                if not isinstance(item, dict):
                    continue
                results.append(
                    HourlyForecast(
                        location=location,
                        forecast_at=_datetime(item.get("fxTime")),
                        temperature_c=int(_string(item.get("temp"))),
                        weather_text=_string(item.get("text")),
                        wind_scale=_string(item.get("windScale")),
                        precipitation_mm=float(_string(item.get("precip")) or "0"),
                        provider_updated_at=updated_at,
                        fetched_at=fetched_at,
                    )
                )
        except (ValueError, TypeError) as exc:
            raise ProviderBadResponseError(
                details={"provider": "qweather", "operation": "weather_hourly"}
            ) from exc
        return FactCollection(
            status=FactStatus.VERIFIED if results else FactStatus.UNAVAILABLE,
            items=results,
        )

    def _parse_warnings(
        self, payload: dict[str, Any], location: Coordinate
    ) -> FactCollection[WeatherWarning]:
        if _string(payload.get("code")) == "204":
            return FactCollection(status=FactStatus.UNAVAILABLE, items=[])
        warnings = payload.get("warning")
        if not isinstance(warnings, list):
            raise ProviderBadResponseError(
                details={"provider": "qweather", "operation": "weather_warning"}
            )
        updated_at = _provider_updated_at(payload, "weather_warning")
        fetched_at = datetime.now(UTC)
        results: list[WeatherWarning] = []
        try:
            for item in warnings:
                if not isinstance(item, dict):
                    continue
                warning_id = _string(item.get("id"))
                title = _string(item.get("title"))
                if not warning_id or not title:
                    continue
                results.append(
                    WeatherWarning(
                        location=location,
                        provider_warning_id=warning_id,
                        sender=_string(item.get("sender")),
                        title=title,
                        severity=_string(item.get("severity")) or "unknown",
                        start_at=_optional_datetime(item.get("startTime")),
                        end_at=_optional_datetime(item.get("endTime")),
                        provider_updated_at=updated_at,
                        fetched_at=fetched_at,
                    )
                )
        except (ValueError, TypeError) as exc:
            raise ProviderBadResponseError(
                details={"provider": "qweather", "operation": "weather_warning"}
            ) from exc
        return FactCollection(status=FactStatus.VERIFIED, items=results)

    def _key(self, operation: str, params: object) -> str:
        return provider_cache_key(
            self._settings.provider_cache_prefix, "qweather", operation, params
        )

    def _cached_collection(
        self, key: str, model: type[ModelT]
    ) -> tuple[FactCollection[ModelT], bool] | None:
        if (
            self._cache is None
            or (hit := self._cache.get(key)) is None
            or not isinstance(hit.payload, dict)
        ):
            return None
        raw_items = hit.payload.get("items")
        raw_status = hit.payload.get("status")
        if not isinstance(raw_items, list) or not isinstance(raw_status, str):
            return None
        try:
            result: FactCollection[ModelT] = FactCollection(
                status=FactStatus(raw_status),
                items=[model.model_validate(item) for item in raw_items],
            )
        except (TypeError, ValueError, ValidationError):
            return None
        return result, hit.is_stale

    def _store_collection(self, key: str, result: ProviderModel, ttl: int) -> None:
        if self._cache is not None:
            self._cache.set(
                key,
                result.model_dump(mode="json"),
                ttl_seconds=ttl,
                stale_ttl_seconds=self._settings.provider_stale_ttl_seconds,
            )


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _datetime(value: object) -> datetime:
    return datetime.fromisoformat(_string(value).replace("Z", "+00:00"))


def _optional_datetime(value: object) -> datetime | None:
    text = _string(value)
    return _datetime(text) if text else None


def _provider_updated_at(payload: dict[str, Any], operation: str) -> datetime:
    try:
        return _datetime(payload.get("updateTime"))
    except ValueError as exc:
        raise ProviderBadResponseError(
            details={"provider": "qweather", "operation": operation}
        ) from exc


def _qweather_problem_type(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""
    type_uri = error.get("type")
    if not isinstance(type_uri, str):
        return ""
    return type_uri.rstrip("/").rsplit("#", maxsplit=1)[-1].rsplit("/", maxsplit=1)[-1]
