from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

import httpx
from pydantic import SecretStr, ValidationError

from ..config import Settings, get_settings
from ..errors import (
    PlaceNotFoundError,
    ProviderAuthFailedError,
    ProviderBadResponseError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    RouteNotFoundError,
)
from .cache import ProviderCache, build_provider_cache, provider_cache_key
from .http import ProviderHttpClient, QueryKeyAuth
from .models import (
    Coordinate,
    GeocodeResult,
    PlaceCandidate,
    ProviderModel,
    RouteResult,
    parse_coordinate,
    with_stale_status,
)
from .poi_quality import classify_semantic_type, normalize_category

ModelT = TypeVar("ModelT", bound=ProviderModel)
TRANSIENT_ERRORS = (ProviderUnavailableError, ProviderRateLimitedError)
AMAP_AUTH_CODES = {"10001", "10002", "10005", "10006", "10007", "10008", "10009"}
AMAP_RATE_CODES = {"10003", "10004", "10019", "10020", "10021", "10022"}
AMAP_UNAVAILABLE_CODES = {"10016", "10017"}


class AmapAdapter:
    def __init__(
        self,
        *,
        api_key: str | SecretStr | None = None,
        cache: ProviderCache | None = None,
        client: httpx.Client | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        secret = api_key or self._settings.amap_api_key
        self._api_key = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        if not self._api_key:
            raise ValueError("AMAP_API_KEY is required")
        self._cache = cache if cache is not None else build_provider_cache(self._settings)
        base_url = self._settings.amap_base_url.strip() or "https://restapi.amap.com"
        self._http = ProviderHttpClient(
            provider="amap",
            base_url=base_url,
            client=client,
            auth=QueryKeyAuth(self._api_key),
            connect_timeout_seconds=self._settings.provider_connect_timeout_seconds,
            read_timeout_seconds=self._settings.provider_read_timeout_seconds,
            max_attempts=self._settings.provider_max_attempts,
            retry_backoff_seconds=self._settings.provider_retry_backoff_seconds,
        )

    def geocode(self, address: str, *, city: str = "") -> GeocodeResult:
        params = {"address": address.strip(), "city": city.strip()}
        key = self._key("geocode", params)
        cached = self._cached_one(key, GeocodeResult)
        if cached is not None and not cached[1]:
            return cached[0]
        try:
            payload = self._request("/v3/geocode/geo", "geocode", {**params, "output": "json"})
            result = self._parse_geocode(payload)
        except TRANSIENT_ERRORS:
            if cached is not None:
                return with_stale_status(cached[0])
            raise
        self._store(key, result, self._settings.geocode_cache_ttl_seconds)
        return result

    def search_places(
        self, keyword: str, *, region: str = "", page_size: int = 10
    ) -> list[PlaceCandidate]:
        params = {"keyword": keyword.strip(), "region": region.strip(), "page_size": page_size}
        key = self._key("poi_search", params)
        cached = self._cached_many(key, PlaceCandidate)
        if cached is not None and not cached[1]:
            return cached[0]
        try:
            payload = self._request(
                "/v5/place/text",
                "poi_search",
                {
                    "keywords": params["keyword"],
                    "region": params["region"],
                    "city_limit": "true" if region else "false",
                    "page_size": str(max(1, min(page_size, 25))),
                    "page_num": "1",
                    "show_fields": "business",
                    "output": "json",
                },
            )
            results = self._parse_places(payload)
            if not results:
                raise PlaceNotFoundError(details={"provider": "amap", "operation": "poi_search"})
        except TRANSIENT_ERRORS:
            if cached is not None:
                return [with_stale_status(item) for item in cached[0]]
            raise
        self._store_many(key, results, self._settings.poi_cache_ttl_seconds)
        return results

    def driving_route(self, origin: Coordinate, destination: Coordinate) -> RouteResult:
        return self._route("driving", origin, destination)

    def walking_route(self, origin: Coordinate, destination: Coordinate) -> RouteResult:
        return self._route("walking", origin, destination)

    def _route(
        self,
        mode: Literal["driving", "walking"],
        origin: Coordinate,
        destination: Coordinate,
    ) -> RouteResult:
        params = {"origin": origin.as_query(), "destination": destination.as_query(), "mode": mode}
        key = self._key(f"route_{mode}", params)
        cached = self._cached_one(key, RouteResult)
        if cached is not None and not cached[1]:
            return cached[0]
        try:
            payload = self._request(
                f"/v5/direction/{mode}",
                f"route_{mode}",
                {
                    "origin": params["origin"],
                    "destination": params["destination"],
                    "show_fields": "cost",
                    "output": "json",
                },
            )
            result = self._parse_route(payload, mode=mode, origin=origin, destination=destination)
        except TRANSIENT_ERRORS:
            if cached is not None:
                return with_stale_status(cached[0])
            raise
        self._store(key, result, self._settings.route_cache_ttl_seconds)
        return result

    def _request(self, path: str, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        return self._http.get_json(
            path,
            operation=operation,
            params={k: str(v) for k, v in params.items()},
            validate=lambda payload: self._validate_response(payload, operation),
        )

    @staticmethod
    def _validate_response(payload: dict[str, Any], operation: str) -> None:
        status = _string(payload.get("status"))
        infocode = _string(payload.get("infocode"))
        if not status or not infocode:
            raise ProviderBadResponseError(details={"provider": "amap", "operation": operation})
        if status == "1" and infocode == "10000":
            return
        details = {
            "provider": "amap",
            "operation": operation,
            "provider_code": infocode or "unknown",
        }
        if infocode in AMAP_AUTH_CODES:
            raise ProviderAuthFailedError(details=details)
        if infocode in AMAP_RATE_CODES:
            raise ProviderRateLimitedError(details=details)
        if infocode in AMAP_UNAVAILABLE_CODES:
            raise ProviderUnavailableError(details=details)
        raise ProviderError(details=details)

    def _parse_geocode(self, payload: dict[str, Any]) -> GeocodeResult:
        geocodes = payload.get("geocodes")
        if not isinstance(geocodes, list) or not geocodes or not isinstance(geocodes[0], dict):
            raise PlaceNotFoundError(details={"provider": "amap", "operation": "geocode"})
        item = geocodes[0]
        try:
            return GeocodeResult(
                formatted_address=_string(item.get("formatted_address")),
                coordinate=parse_coordinate(item.get("location")),
                match_level=_string(item.get("level")),
                district=_string(item.get("district")),
                adcode=_string(item.get("adcode")),
                fetched_at=datetime.now(UTC),
            )
        except (ValueError, TypeError) as exc:
            raise ProviderBadResponseError(
                details={"provider": "amap", "operation": "geocode"}
            ) from exc

    def _parse_places(self, payload: dict[str, Any]) -> list[PlaceCandidate]:
        pois = payload.get("pois")
        if not isinstance(pois, list):
            raise ProviderBadResponseError(details={"provider": "amap", "operation": "poi_search"})
        results: list[PlaceCandidate] = []
        now = datetime.now(UTC)
        for item in pois:
            if not isinstance(item, dict):
                continue
            try:
                provider_id = _string(item.get("id"))
                name = _string(item.get("name"))
                category_raw = _string(item.get("type"))
                if not provider_id or not name:
                    continue
                results.append(
                    PlaceCandidate(
                        provider_place_id=provider_id,
                        name=name,
                        address=_string(item.get("address")),
                        province=_string(item.get("pname")),
                        city=_string(item.get("cityname")),
                        district=_string(item.get("adname")),
                        adcode=_string(item.get("adcode")),
                        coordinate=parse_coordinate(item.get("location")),
                        category_raw=category_raw,
                        category_normalized=normalize_category(category_raw),
                        semantic_type=classify_semantic_type(name, category_raw),
                        fetched_at=now,
                    )
                )
            except (ValueError, TypeError):
                continue
        return results

    def _parse_route(
        self,
        payload: dict[str, Any],
        *,
        mode: Literal["driving", "walking"],
        origin: Coordinate,
        destination: Coordinate,
    ) -> RouteResult:
        route = payload.get("route")
        paths = route.get("paths") if isinstance(route, dict) else None
        if not isinstance(paths, list) or not paths or not isinstance(paths[0], dict):
            raise RouteNotFoundError(details={"provider": "amap", "operation": f"route_{mode}"})
        path = paths[0]
        cost_raw = path.get("cost")
        cost: dict[str, Any] = cost_raw if isinstance(cost_raw, dict) else {}
        try:
            return RouteResult(
                mode=mode,
                origin=origin,
                destination=destination,
                distance_meters=_int_value(path.get("distance")),
                duration_seconds=_int_value(cost.get("duration", path.get("duration"))),
                tolls_yuan=_optional_float(cost.get("tolls")) if mode == "driving" else None,
                traffic_lights=_optional_int(cost.get("traffic_lights"))
                if mode == "driving"
                else None,
                fetched_at=datetime.now(UTC),
            )
        except (ValueError, TypeError) as exc:
            raise ProviderBadResponseError(
                details={"provider": "amap", "operation": f"route_{mode}"}
            ) from exc

    def _key(self, operation: str, params: object) -> str:
        return provider_cache_key(self._settings.provider_cache_prefix, "amap", operation, params)

    def _cached_one(self, key: str, model: type[ModelT]) -> tuple[ModelT, bool] | None:
        if self._cache is None or (hit := self._cache.get(key)) is None:
            return None
        try:
            return model.model_validate(hit.payload), hit.is_stale
        except ValidationError:
            return None

    def _cached_many(self, key: str, model: type[ModelT]) -> tuple[list[ModelT], bool] | None:
        if (
            self._cache is None
            or (hit := self._cache.get(key)) is None
            or not isinstance(hit.payload, list)
        ):
            return None
        try:
            return [model.model_validate(item) for item in hit.payload], hit.is_stale
        except ValidationError:
            return None

    def _store(self, key: str, model: ProviderModel, ttl: int) -> None:
        if self._cache is not None:
            self._cache.set(
                key,
                model.model_dump(mode="json"),
                ttl_seconds=ttl,
                stale_ttl_seconds=self._settings.provider_stale_ttl_seconds,
            )

    def _store_many(self, key: str, models: Sequence[ProviderModel], ttl: int) -> None:
        if self._cache is not None:
            self._cache.set(
                key,
                [item.model_dump(mode="json") for item in models],
                ttl_seconds=ttl,
                stale_ttl_seconds=self._settings.provider_stale_ttl_seconds,
            )


def _string(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    return ""


def _int_value(value: object) -> int:
    return int(float(_string(value)))


def _optional_int(value: object) -> int | None:
    text = _string(value)
    return int(float(text)) if text else None


def _optional_float(value: object) -> float | None:
    text = _string(value)
    return float(text) if text else None
