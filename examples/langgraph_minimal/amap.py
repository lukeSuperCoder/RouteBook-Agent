from __future__ import annotations

import os
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv


log = logging.getLogger("routebook.amap")


class AmapError(RuntimeError):
    """Base error for normalized Amap failures."""


class AmapAuthError(AmapError):
    pass


class AmapRateLimitError(AmapError):
    pass


class PlaceNotFoundError(AmapError):
    pass


@dataclass(frozen=True)
class PlaceCandidate:
    provider_place_id: str
    name: str
    address: str
    district: str
    longitude: float
    latitude: float
    category: str

    def to_state(self) -> dict[str, Any]:
        return {
            "id": self.provider_place_id,
            "provider": "amap",
            "name": self.name,
            "address": self.address,
            "district": self.district,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "coordinate_system": "GCJ-02",
            "category": self.category,
            "status": "verified",
        }


def _string_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    return ""


def parse_place_response(payload: Any, keyword: str) -> list[PlaceCandidate]:
    if not isinstance(payload, dict):
        raise AmapError("高德返回了无法识别的响应。")

    status = str(payload.get("status", ""))
    infocode = str(payload.get("infocode", ""))
    if status != "1" or infocode != "10000":
        info = _string_value(payload.get("info")) or "未知错误"
        if infocode in {"10001", "10002", "10007", "10009"}:
            raise AmapAuthError(f"高德鉴权失败（{infocode}: {info}）。")
        if infocode in {"10003", "10004", "10020", "10021"}:
            raise AmapRateLimitError(
                f"高德调用额度或频率已超限（{infocode}: {info}），请稍后重试或检查配额。"
            )
        raise AmapError(f"高德服务错误（{infocode or 'unknown'}: {info}）。")

    raw_pois = payload.get("pois", [])
    if not isinstance(raw_pois, list):
        raise AmapError("高德 pois 字段格式异常。")

    candidates: list[PlaceCandidate] = []
    for poi in raw_pois:
        if not isinstance(poi, dict):
            continue
        location = _string_value(poi.get("location"))
        try:
            longitude_text, latitude_text = location.split(",", maxsplit=1)
            longitude = float(longitude_text)
            latitude = float(latitude_text)
        except (TypeError, ValueError):
            continue

        provider_place_id = _string_value(poi.get("id"))
        name = _string_value(poi.get("name"))
        if not provider_place_id or not name:
            continue
        candidates.append(
            PlaceCandidate(
                provider_place_id=provider_place_id,
                name=name,
                address=_string_value(poi.get("address")),
                district=_string_value(poi.get("adname")),
                longitude=longitude,
                latitude=latitude,
                category=_string_value(poi.get("type")),
            )
        )

    if not candidates:
        raise PlaceNotFoundError(f"高德未找到地点：{keyword}")

    # 精确同名结果优先；若只有一个精确结果，可安全自动采用。
    exact_matches = [item for item in candidates if item.name.strip() == keyword.strip()]
    return exact_matches or candidates


class AmapPlaceSearcher:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        min_request_interval: float = 0.6,
    ) -> None:
        load_dotenv(override=True)
        self._api_key = api_key or os.getenv("AMAP_API_KEY")
        self._base_url = (
            base_url or os.getenv("AMAP_BASE_URL") or "https://restapi.amap.com"
        ).rstrip("/")
        if not self._api_key:
            raise RuntimeError(
                "缺少 AMAP_API_KEY。请在 .env 中填写高德 Web 服务 API Key。"
            )
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(8.0, connect=3.0),
            transport=httpx.HTTPTransport(retries=2),
        )
        self._min_request_interval = min_request_interval
        self._last_request_at: float | None = None

    def search(self, keyword: str, region: str) -> list[dict[str, Any]]:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._min_request_interval - elapsed
            if remaining > 0:
                log.info("高德请求限速 wait=%.2fs", remaining)
                time.sleep(remaining)
        log.info("搜索高德 POI keyword=%s region=%s city_limit=true", keyword, region)
        try:
            self._last_request_at = time.monotonic()
            response = self._client.get(
                f"{self._base_url}/v5/place/text",
                params={
                    "key": self._api_key,
                    "keywords": keyword,
                    "region": region,
                    "city_limit": "true",
                    "page_size": 5,
                    "page_num": 1,
                    "output": "json",
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise AmapError("高德地点搜索超时，请稍后重试。") from exc
        except httpx.HTTPError as exc:
            raise AmapError("高德地点搜索网络请求失败。") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise AmapError("高德返回了非 JSON 响应。") from exc
        places = [item.to_state() for item in parse_place_response(payload, keyword)]
        log.info(
            "高德 POI 搜索完成 keyword=%s candidates=%d names=%s",
            keyword,
            len(places),
            [place["name"] for place in places],
        )
        return places
