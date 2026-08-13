from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

import redis

from ..config import Settings

log = logging.getLogger("routebook.providers.cache")


@dataclass(frozen=True)
class CacheLookup:
    payload: object
    is_stale: bool


class ProviderCache(Protocol):
    def get(self, key: str) -> CacheLookup | None: ...

    def set(
        self, key: str, payload: object, *, ttl_seconds: int, stale_ttl_seconds: int
    ) -> None: ...


def provider_cache_key(prefix: str, provider: str, operation: str, params: object) -> str:
    canonical = json.dumps(params, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{prefix}:{provider}:{operation}:{digest}"


class InMemoryProviderCache:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._values: dict[str, tuple[object, float, float]] = {}

    def get(self, key: str) -> CacheLookup | None:
        record = self._values.get(key)
        if record is None:
            return None
        payload, fresh_until, stale_until = record
        now = float(self._clock())
        if now > stale_until:
            self._values.pop(key, None)
            return None
        result = CacheLookup(payload=payload, is_stale=now > fresh_until)
        log.info("provider cache hit key=%s stale=%s", key, result.is_stale)
        return result

    def set(self, key: str, payload: object, *, ttl_seconds: int, stale_ttl_seconds: int) -> None:
        now = float(self._clock())
        self._values[key] = (payload, now + ttl_seconds, now + ttl_seconds + stale_ttl_seconds)


class RedisProviderCache:
    def __init__(self, redis_url: str, *, timeout_seconds: float = 0.25) -> None:
        self._client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
        )

    def get(self, key: str) -> CacheLookup | None:
        try:
            raw = self._client.get(key)
        except redis.RedisError:
            log.warning("provider cache unavailable operation=get key=%s", key)
            return None
        if raw is None:
            return None
        try:
            record = json.loads(cast(str, raw))
        except (TypeError, ValueError):
            log.warning("provider cache value invalid key=%s", key)
            return None
        if not isinstance(record, dict):
            return None
        try:
            fresh_until = float(record.get("fresh_until", 0))
        except (TypeError, ValueError):
            log.warning("provider cache metadata invalid key=%s", key)
            return None
        result = CacheLookup(payload=record.get("payload"), is_stale=time.time() > fresh_until)
        log.info("provider cache hit key=%s stale=%s", key, result.is_stale)
        return result

    def set(self, key: str, payload: object, *, ttl_seconds: int, stale_ttl_seconds: int) -> None:
        now = time.time()
        value = json.dumps(
            {"fresh_until": now + ttl_seconds, "payload": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            self._client.setex(key, ttl_seconds + stale_ttl_seconds, value)
        except redis.RedisError:
            log.warning("provider cache unavailable operation=set key=%s", key)


def build_provider_cache(settings: Settings) -> ProviderCache | None:
    if not settings.provider_cache_enabled:
        return None
    return RedisProviderCache(
        settings.redis_url,
        timeout_seconds=settings.provider_cache_timeout_seconds,
    )
