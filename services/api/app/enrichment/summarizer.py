from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from anthropic import Anthropic

from ..config import Settings, get_settings
from .provider import SearchResult


@dataclass(frozen=True)
class PlaceSearchMaterial:
    place_id: UUID
    place_name: str
    results: list[SearchResult]


class EnrichmentSummarizer(Protocol):
    def summarize_all(
        self, *, city: str, materials: list[PlaceSearchMaterial]
    ) -> dict[UUID, str]: ...


SYSTEM_PROMPT = """你是旅行信息编辑。根据联网搜索结果，
为指定地点整理一段简洁、自然、可直接展示的中文摘要。

规则：
1. 如果搜索结果包含开放时间、门票、预约方式、推荐路线或推荐游玩内容，优先整理这些实用信息。
2. 如果没有上述信息，也必须根据搜索结果已有的摘要内容，汇总一段地点介绍或游玩参考，不能返回空内容。
3. 只整理输入中出现的信息，不补造具体时间、价格、预约规则或路线。
4. 不评价来源真实性，不输出“已验证”“未经验证”“据某网站”等标签，不使用 Markdown。
5. 合并重复内容，控制在 180 个汉字以内，直接输出适合行程卡片展示的文案。
"""


class AnthropicEnrichmentSummarizer:
    def __init__(self, settings: Settings | None = None, client: Anthropic | None = None) -> None:
        self.settings = settings or get_settings()
        key = self.settings.anthropic_api_key
        if client is None and key is None:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")
        self.client = client or Anthropic(
            api_key=key.get_secret_value() if key else "",
            base_url=self.settings.anthropic_base_url,
            timeout=self.settings.requirement_timeout_seconds,
            max_retries=0,
        )

    def summarize_all(
        self, *, city: str, materials: list[PlaceSearchMaterial]
    ) -> dict[UUID, str]:
        material = "\n\n".join(
            f"地点ID：{item.place_id}\n地点：{item.place_name}\n"
            + "\n".join(
                f"搜索结果{index}：{result.title}\n摘要：{result.snippet}"
                for index, result in enumerate(item.results, start=1)
            )
            for item in materials
        )
        response = self.client.messages.create(
            model=self.settings.model_id,
            max_tokens=min(4000, max(800, len(materials) * 400)),
            temperature=0,
            system=(
                SYSTEM_PROMPT
                + "\n一次处理输入中的全部地点。只返回 JSON 对象，key 必须是地点ID，"
                "value 是该地点的攻略摘要；不得遗漏任何有搜索结果的地点。"
            ),
            messages=[
                {
                    "role": "user",
                    "content": f"目的地：{city}\n\n全部地点搜索结果：\n{material}",
                }
            ],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise ValueError("model response has no text output")
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match is None:
            raise ValueError("model response has no JSON object")
        payload = json.loads(match.group(0))
        return {
            UUID(place_id): str(summary).strip()[:500]
            for place_id, summary in payload.items()
            if str(summary).strip()
        }
