from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv


log = logging.getLogger("routebook.ai")


EXTRACT_REQUIREMENTS_TOOL = {
    "name": "extract_travel_requirements",
    "description": "从用户消息中提取生成路书所需的结构化旅行需求。",
    "input_schema": {
        "type": "object",
        "properties": {
            "destination": {
                "type": "string",
                "description": "旅行目的地城市；未提供时返回空字符串。",
            },
            "days": {
                "type": "integer",
                "description": "旅行天数；未提供时返回 0。",
            },
            "must_visit": {
                "type": "array",
                "items": {"type": "string"},
                "description": "用户明确提出想去或必须去的地点。",
            },
            "suggested_visit": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "AI 补充推荐的代表性地点。用户未指定景点时推荐 3-5 个；"
                    "已指定时仅在行程明显过少时补充，且不能与 must_visit 重复。"
                ),
            },
        },
        "required": ["destination", "days", "must_visit", "suggested_visit"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class TravelRequirements:
    destination: str
    days: int
    must_visit: list[str]
    suggested_visit: list[str] = field(default_factory=list)


def parse_tool_input(value: Any) -> TravelRequirements:
    if not isinstance(value, dict):
        raise ValueError("AI 返回的旅行需求不是 JSON 对象。")

    destination = value.get("destination")
    days = value.get("days")
    must_visit = value.get("must_visit")
    suggested_visit = value.get("suggested_visit", [])
    if not isinstance(destination, str):
        raise ValueError("AI 返回的 destination 必须是字符串。")
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        raise ValueError("AI 返回的 days 必须是非负整数。")
    if not isinstance(must_visit, list) or not all(
        isinstance(place, str) for place in must_visit
    ):
        raise ValueError("AI 返回的 must_visit 必须是字符串数组。")
    if not isinstance(suggested_visit, list) or not all(
        isinstance(place, str) for place in suggested_visit
    ):
        raise ValueError("AI 返回的 suggested_visit 必须是字符串数组。")

    normalized_must_visit = [place.strip() for place in must_visit if place.strip()]
    must_visit_names = set(normalized_must_visit)
    normalized_suggestions = [
        place.strip()
        for place in suggested_visit
        if place.strip() and place.strip() not in must_visit_names
    ]
    # 原型按“一天一个主要地点”控制外部查询次数；用户明确指定的地点不裁剪。
    suggestion_slots = max(days - len(normalized_must_visit), 0)
    normalized_suggestions = normalized_suggestions[:suggestion_slots]

    return TravelRequirements(
        destination=destination.strip(),
        days=days,
        must_visit=normalized_must_visit,
        suggested_visit=normalized_suggestions,
    )


class AnthropicRequirementExtractor:
    def __init__(self) -> None:
        load_dotenv(override=True)
        api_key = os.getenv("ANTHROPIC_API_KEY")
        model = os.getenv("MODEL_ID", "glm-5")
        base_url = os.getenv(
            "ANTHROPIC_BASE_URL",
            "https://open.bigmodel.cn/api/anthropic",
        )
        if not api_key:
            raise RuntimeError(
                "缺少 ANTHROPIC_API_KEY。请复制 .env.example 为 .env 并填写 API Key。"
            )

        # 与参考代码一致：自定义 Anthropic 兼容端点时不使用 auth token。
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        self._client = Anthropic(api_key=api_key, base_url=base_url)
        self._model = model

    def __call__(self, message: str) -> TravelRequirements:
        log.info("调用模型提取需求 model=%s message_length=%d", self._model, len(message))
        response = self._client.messages.create(
            model=self._model,
            max_tokens=800,
            system=(
                "你是路书需求分析与初步推荐助手。目的地、天数和 must_visit 只能提取"
                "用户明确表达的信息。若用户提供了目的地但未指定景点，请在 suggested_visit "
                "中按一天一个主要地点推荐，推荐数量不要超过旅行天数；若用户指定的"
                "地点过少，可以少量补充。只返回可供高德 POI 搜索的具体地点名称，不要返回"
                "泛化活动、餐饮类型或虚构地点。必须调用 extract_travel_requirements 工具。"
            ),
            messages=[{"role": "user", "content": message}],
            tools=[EXTRACT_REQUIREMENTS_TOOL],
            tool_choice={"type": "tool", "name": "extract_travel_requirements"},
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "extract_travel_requirements":
                result = parse_tool_input(block.input)
                log.info(
                    "需求提取完成 destination=%s days=%d must_visit=%s suggested_visit=%s",
                    result.destination or "未提供",
                    result.days,
                    result.must_visit,
                    result.suggested_visit,
                )
                return result
        raise RuntimeError("模型没有调用 extract_travel_requirements 工具。")
