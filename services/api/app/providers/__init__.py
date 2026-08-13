"""Normalized external-fact adapters used by RouteBook domain services."""

from .amap import AmapAdapter
from .cache import InMemoryProviderCache, RedisProviderCache, build_provider_cache
from .place_service import PlaceFactService
from .qweather import QWeatherAdapter

__all__ = [
    "AmapAdapter",
    "InMemoryProviderCache",
    "PlaceFactService",
    "QWeatherAdapter",
    "RedisProviderCache",
    "build_provider_cache",
]
