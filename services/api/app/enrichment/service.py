from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from ..enums import FactStatus
from ..providers.cache import ProviderCache, provider_cache_key
from ..schemas import (
    ContentSourceSnapshot,
    PlaceEnrichmentSnapshot,
    PlaceHighlightSnapshot,
    PlaceSnapshot,
)
from .provider import SearchProvider, SearchRequest, SearchResponse
from .summarizer import EnrichmentSummarizer, PlaceSearchMaterial


@dataclass
class EnrichmentBudget:
    max_search_requests: int = 12
    max_places_enriched: int = 12
    max_searches_per_place: int = 1
    max_results_per_search: int = 5
    used_search_requests: int = 0

    def consume(self) -> bool:
        if self.used_search_requests >= self.max_search_requests:
            return False
        self.used_search_requests += 1
        return True


_P0 = (
    "景区",
    "博物馆",
    "博物院",
    "纪念馆",
    "美术馆",
    "乐园",
    "剧院",
    "演出",
    "展览",
    "动物园",
    "植物园",
)
_P1 = ("公园", "寺", "观景", "市场", "古镇", "古城", "遗址")
_TRUSTED_TRAVEL_HOSTS = (
    "trip.com",
    "ctrip.com",
    "mafengwo.cn",
    "qyer.com",
    "klook.com",
)
_CONTENT_PLATFORM_HOSTS = ("weibo.com", "xiaohongshu.com", "douyin.com", "zhihu.com")
_ACTION_KEYWORDS = (
    "开放时间",
    "开放入馆",
    "停止入馆",
    "停止售票",
    "闭馆",
    "预约",
    "门票",
    "票价",
    "免费",
    "建议游玩",
    "建议参观",
    "参观时长",
    "游览路线",
    "推荐路线",
    "推荐参观",
    "必看",
    "登顶",
    "观景",
    "入口",
    "出口",
    "排队",
)
_FACT_PATTERNS = (
    (
        "closure",
        "闭馆",
        re.compile(
            r"[^。；\n]{0,18}(?:周[一二三四五六日天]闭馆|每周[一二三四五六日天]闭馆|暂停开放|临时关闭)[^。；\n]{0,35}"
        ),
    ),
    (
        "opening_hours",
        "开放",
        re.compile(
            r"(?:开放时间[：:]?\s*)?(?:[0-2]?\d[:：][0-5]\d)\s*[-—至~]\s*(?:[0-2]?\d[:：][0-5]\d)"
        ),
    ),
    ("last_entry", "入场", re.compile(r"(?:停止入场|停止售票)[^。；\n]{0,30}")),
    (
        "ticket",
        "门票",
        re.compile(r"(?:门票|票价|成人票)[^。；\n]{0,35}(?:免费|\d+(?:\.\d+)?\s*元)"),
    ),
    (
        "reservation",
        "预约",
        re.compile(r"[^。；\n]{0,18}(?:实名预约|提前预约|无需预约|需预约)[^。；\n]{0,35}"),
    ),
    (
        "duration",
        "时长",
        re.compile(
            r"(?:建议|推荐)?(?:游玩|参观)(?:时间|时长)?[^。；\n]{0,20}\d+(?:\.\d+)?\s*(?:小时|分钟)"
        ),
    ),
)


def place_priority(place: PlaceSnapshot) -> int | None:
    text = f"{place.semantic_type} {place.category_normalized} {place.name}"
    if any(token in text for token in _P0):
        return 0
    if any(token in text for token in _P1):
        return 1
    return None


class PlaceEnrichmentService:
    def __init__(
        self,
        *,
        provider: SearchProvider,
        cache: ProviderCache | None = None,
        summarizer: EnrichmentSummarizer | None = None,
        cache_prefix: str = "routebook:enrichment:v8",
        ttl_seconds: int = 604800,
    ) -> None:
        self.provider = provider
        self.summarizer = summarizer
        self.cache = cache
        self.cache_prefix = cache_prefix
        self.ttl_seconds = ttl_seconds

    def enrich(
        self,
        *,
        routebook_id: UUID,
        city: str,
        places: list[PlaceSnapshot],
        request_id: str,
        budget: EnrichmentBudget,
    ) -> list[PlaceEnrichmentSnapshot]:
        selected = sorted(
            (
                (place_priority(p), i, p)
                for i, p in enumerate(places)
                if place_priority(p) is not None
            ),
            key=lambda x: (x[0], x[1]),
        )[: budget.max_places_enriched]
        output_by_place: dict[UUID, PlaceEnrichmentSnapshot] = {}
        pending: list[tuple[PlaceSnapshot, str, SearchResponse]] = []
        included_place_ids: list[UUID] = []
        for _, _, place in selected:
            key = provider_cache_key(
                self.cache_prefix,
                "zhipu_web_search_prime",
                "place",
                {
                    "provider": place.provider,
                    "provider_place_id": place.provider_place_id,
                    "city": city,
                    "schema": 1,
                },
            )
            cached = self.cache.get(key) if self.cache else None
            if cached and not cached.is_stale and isinstance(cached.payload, dict):
                output_by_place[place.id] = PlaceEnrichmentSnapshot.model_validate(cached.payload)
                included_place_ids.append(place.id)
                continue
            if not budget.consume():
                break
            response = self.provider.search(
                SearchRequest(
                    query=f"{city} {place.name} 官方 开放时间 停止入场 门票 预约 参观须知 游玩建议",
                    max_results=budget.max_results_per_search,
                    request_id=f"{request_id}-{budget.used_search_requests}",
                    routebook_id=routebook_id,
                    place_id=place.id,
                )
            )
            pending.append((place, key, response))
            included_place_ids.append(place.id)

        generated: dict[UUID, str] = {}
        materials = [
            PlaceSearchMaterial(
                place_id=place.id,
                place_name=place.name,
                results=response.results[:3],
            )
            for place, _, response in pending
            if response.results
        ]
        if materials and self.summarizer:
            try:
                generated = self.summarizer.summarize_all(city=city, materials=materials)
            except Exception:
                generated = {}

        for place, key, response in pending:
            item = self._extract(place, response, generated.get(place.id))
            output_by_place[place.id] = item
            if self.cache:
                self.cache.set(
                    key,
                    item.model_dump(mode="json"),
                    ttl_seconds=self.ttl_seconds,
                    stale_ttl_seconds=0,
                )
        return [output_by_place[place_id] for place_id in included_place_ids]

    def _extract(
        self, place: PlaceSnapshot, response: SearchResponse, generated_guide: str | None = None
    ) -> PlaceEnrichmentSnapshot:
        now = datetime.now(UTC)
        candidates = response.results
        sources: list[ContentSourceSnapshot] = []
        facts: dict[str, list[tuple[str, str, float]]] = {}
        action_parts: list[str] = []
        for result in candidates[:3]:
            source_id = f"source-{len(sources) + 1}"
            host = result.url.host or ""
            normalized_name = place.name.replace(" ", "").lower()
            normalized_site = (result.site_name or "").replace(" ", "").lower()
            if host.endswith("gov.cn"):
                source_type = "government"
            elif normalized_name in normalized_site:
                source_type = "official"
            elif any(host == item or host.endswith(f".{item}") for item in _TRUSTED_TRAVEL_HOSTS):
                source_type = "trusted_travel_platform"
            else:
                source_type = "unknown"
            sources.append(
                ContentSourceSnapshot(
                    id=source_id,
                    title=result.title,
                    url=str(result.url),
                    site_name=result.site_name,
                    source_type=source_type,
                    published_at=result.published_at,
                    retrieved_at=now,
                )
            )
            text = result.snippet.replace("\n", " ")
            for fact_type, _, pattern in _FACT_PATTERNS:
                match = pattern.search(text)
                if match:
                    facts.setdefault(fact_type, []).append(
                        (
                            match.group(0).strip(" ，。；"),
                            source_id,
                            0.96 if source_type in {"official", "government"} else 0.72,
                        )
                    )
            action_parts.extend(self._action_sentences(text))
        highlights: list[PlaceHighlightSnapshot] = []
        labels = {kind: label for kind, label, _ in _FACT_PATTERNS}
        for kind, values in facts.items():
            text, source_id, confidence = max(values, key=lambda value: value[2])
            highlights.append(
                PlaceHighlightSnapshot(
                    type=kind,
                    label=labels[kind],
                    text=text[:200],
                    source_ids=[source_id],
                    confidence=confidence,
                    status=FactStatus.UNVERIFIED,
                )
            )
        fact_summaries = [
            f"{item.label}：{item.text}"
            for item in highlights
            if not any(item.text in sentence for sentence in action_parts)
        ]
        fallback_parts = self._unique_texts(
            [
                *action_parts,
                *fact_summaries,
                *[self._clean_snippet(item.snippet) for item in candidates],
            ]
        )
        fallback = "；".join(part for part in fallback_parts if part)[:500]
        guide = generated_guide[:500] if generated_guide else fallback or None
        summary = guide[:180] if guide else None
        status = FactStatus.UNVERIFIED if guide else FactStatus.UNAVAILABLE
        return PlaceEnrichmentSnapshot(
            place_id=place.id,
            summary=summary,
            guide_text=guide,
            highlights=highlights[:5],
            tips=[],
            sources=sources,
            generated_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            status=status,
        )

    @staticmethod
    def _registrable_domain(host: str) -> str:
        labels = host.lower().strip(".").split(".")
        if len(labels) >= 3 and labels[-2:] in [["com", "cn"], ["org", "cn"], ["gov", "cn"]]:
            return ".".join(labels[-3:])
        return ".".join(labels[-2:])

    @staticmethod
    def _action_sentences(text: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", text).strip()
        normalized = re.sub(
            r"(\d{1,2}:\d{2})\.\s*开放入馆时间\.\s*(\d{1,2}:\d{2})\.\s*"
            r"停止入馆时间\.\s*(\d{1,2}:\d{2})\.\s*闭馆",
            r"开放入馆时间 \1；停止入馆时间 \2；闭馆时间 \3",
            normalized,
        )
        normalized = re.sub(r"\.{2,}|…+", "", normalized)
        sentences = re.split(r"(?<=[。！？；｡])\s*|\.(?!\d)\s*|\s*[·|｜]\s*", normalized)
        return [
            sentence.strip(" ，。；.-")[:180]
            for sentence in sentences
            if any(keyword in sentence for keyword in _ACTION_KEYWORDS)
            and len(sentence.strip()) >= 8
            and not sentence.strip().endswith(("：", ":"))
        ]

    @staticmethod
    def _clean_snippet(text: str) -> str:
        normalized = re.sub(r"\s+", " ", text).strip(" ，。；.-")
        normalized = re.sub(r"\.{2,}|…+", "", normalized)
        return normalized[:240]

    @staticmethod
    def _unique_texts(items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            normalized = re.sub(r"\s+", "", item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(item)
        return result
