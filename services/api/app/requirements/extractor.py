from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from typing import Protocol

from anthropic import Anthropic

from ..config import Settings, get_settings
from ..schemas import RequirementSnapshot
from .models import ExtractionResult, ExtractionTrace, RequirementPatch
from .prompts import REQUIREMENT_EXTRACTION_SYSTEM_PROMPT

log = logging.getLogger("routebook.requirements.extractor")
EXTRACTION_TOOL_NAME = "extract_requirement_patch"


class RequirementExtractor(Protocol):
    def extract(
        self,
        message: str,
        current: RequirementSnapshot,
        *,
        today: date,
    ) -> ExtractionResult: ...


class RequirementExtractionError(RuntimeError):
    pass


class AnthropicRequirementExtractor:
    def __init__(self, settings: Settings | None = None, client: Anthropic | None = None) -> None:
        self.settings = settings or get_settings()
        key = self.settings.anthropic_api_key
        if client is None and key is None:
            raise RequirementExtractionError("ANTHROPIC_API_KEY is not configured")
        self.client = client or Anthropic(
            api_key=key.get_secret_value() if key else "",
            base_url=self.settings.anthropic_base_url,
            timeout=self.settings.requirement_timeout_seconds,
            max_retries=0,
        )

    def extract(
        self,
        message: str,
        current: RequirementSnapshot,
        *,
        today: date,
    ) -> ExtractionResult:
        started = time.monotonic()
        last_error: Exception | None = None
        repair_note = ""
        for attempt in range(1, self.settings.requirement_max_attempts + 1):
            try:
                input_text = self._input(message, current, today, repair_note)
                patch, response_id, input_tokens, output_tokens = (
                    self._native_extract(input_text)
                    if attempt == 1
                    else self._tool_extract(input_text)
                )
                elapsed = int((time.monotonic() - started) * 1000)
                log.info(
                    "requirement extraction succeeded model=%s prompt_version=%s "
                    "attempt=%d latency_ms=%d response_id=%s",
                    self.settings.model_id,
                    self.settings.requirement_prompt_version,
                    attempt,
                    elapsed,
                    response_id,
                )
                return ExtractionResult(
                    patch=patch,
                    trace=ExtractionTrace(
                        prompt_version=self.settings.requirement_prompt_version,
                        model=self.settings.model_id,
                        response_id=response_id,
                        attempt_count=attempt,
                        latency_ms=elapsed,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        recorded_at=datetime.now(UTC),
                    ),
                )
            except Exception as exc:
                last_error = exc
                repair_note = "上一次输出未通过严格 Schema 校验，请只修复格式和字段枚举。"
                log.warning(
                    "requirement extraction attempt failed model=%s prompt_version=%s attempt=%d "
                    "error_type=%s",
                    self.settings.model_id,
                    self.settings.requirement_prompt_version,
                    attempt,
                    type(exc).__name__,
                )
        raise RequirementExtractionError("structured requirement extraction failed") from last_error

    def _native_extract(self, input_text: str) -> tuple[RequirementPatch, str, int, int]:
        response = self.client.messages.parse(
            model=self.settings.model_id,
            max_tokens=1600,
            temperature=0,
            system=REQUIREMENT_EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": input_text}],
            output_format=RequirementPatch,
        )
        patch = response.parsed_output
        if patch is None:
            raise ValueError("model response has no parsed output")
        return (
            patch,
            response.id,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

    def _tool_extract(self, input_text: str) -> tuple[RequirementPatch, str, int, int]:
        response = self.client.messages.create(
            model=self.settings.model_id,
            max_tokens=1600,
            temperature=0,
            system=(
                REQUIREMENT_EXTRACTION_SYSTEM_PROMPT
                + "\n必须调用 extract_requirement_patch 工具，不要输出 Markdown 或普通文本。"
            ),
            messages=[{"role": "user", "content": input_text}],
            tools=[
                {
                    "name": EXTRACTION_TOOL_NAME,
                    "description": "提交本轮用户消息中可验证的旅行需求增量。",
                    "input_schema": RequirementPatch.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": EXTRACTION_TOOL_NAME},
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == EXTRACTION_TOOL_NAME:
                return (
                    RequirementPatch.model_validate(block.input),
                    response.id,
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
        raise ValueError("model response did not call requirement extraction tool")

    @staticmethod
    def _input(
        message: str,
        current: RequirementSnapshot,
        today: date,
        repair_note: str,
    ) -> str:
        current_json = current.model_dump_json(exclude_none=True)
        return (
            f"[当前日期]\n{today.isoformat()}\n\n"
            f"[已确认业务状态]\n{current_json}\n\n"
            f"[本轮用户消息]\n{message}\n\n"
            f"[修复提示]\n{repair_note or '无'}"
        )
