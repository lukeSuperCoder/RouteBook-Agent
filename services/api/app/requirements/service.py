from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, cast

from ..enums import RequirementSource
from ..schemas import RequirementSnapshot
from .models import (
    BlockingCode,
    BlockingIssue,
    ClarificationQuestion,
    RequirementConflict,
    RequirementDecision,
    RequirementField,
    RequirementPatch,
)

DEFAULTS: dict[str, object] = {
    "companions": [],
    "themes": [],
    "intensity": "moderate",
    "crowd_tolerance": "medium",
    "suburban_acceptance": False,
    "must_visit_place_texts": [],
    "optional_place_texts": [],
    "excluded_place_texts": [],
    "visited_place_texts": [],
    "notes": [],
}
PLACE_TEXT_FIELDS = {
    "must_visit_place_texts",
    "optional_place_texts",
    "excluded_place_texts",
    "visited_place_texts",
}
LIST_FIELDS = PLACE_TEXT_FIELDS | {"companions", "themes", "notes"}


class RequirementService:
    def __init__(self, *, today: date | None = None) -> None:
        self.today = today or date.today()

    def apply(
        self,
        current: RequirementSnapshot,
        patch: RequirementPatch,
        *,
        extraction_failed: bool = False,
    ) -> RequirementDecision:
        data = current.model_dump(mode="python")
        conflicts: list[RequirementConflict] = []

        for field_name in RequirementPatch.model_fields:
            if field_name == "ambiguities":
                continue
            incoming = getattr(patch, field_name)
            if incoming is None:
                continue
            current_value = data[field_name]
            if field_name in PLACE_TEXT_FIELDS and incoming.source == RequirementSource.INFERRED:
                conflicts.append(
                    RequirementConflict(
                        field=cast(RequirementField, field_name),
                        current_value=current_value.get("value"),
                        proposed_value=incoming.value,
                        reason="inferred_place_cannot_be_requirement",
                        message="模型推断的地点不能写入用户需求。",
                    )
                )
                continue
            if (
                incoming.source == RequirementSource.INFERRED
                and (current_value.get("confirmed") or current_value.get("source") == "explicit")
            ):
                conflicts.append(
                    RequirementConflict(
                        field=cast(RequirementField, field_name),
                        current_value=current_value.get("value"),
                        proposed_value=incoming.value,
                        reason="inferred_cannot_override_confirmed",
                        message="推断值不能覆盖用户已确认值。",
                    )
                )
                continue
            value = self._normalize(field_name, incoming.value)
            if field_name in LIST_FIELDS:
                value = self._apply_list_operation(
                    current_value.get("value"), value, incoming.operation
                )
            data[field_name] = {
                "value": value,
                "source": incoming.source.value,
                "confidence": incoming.confidence,
                "confirmed": incoming.source == RequirementSource.EXPLICIT,
            }

        for ambiguity in patch.ambiguities:
            conflicts.append(
                RequirementConflict(
                    field=ambiguity.field,
                    current_value=data[ambiguity.field].get("value"),
                    proposed_value=ambiguity.candidates,
                    reason="ambiguous_input",
                    message=ambiguity.reason,
                )
            )

        for field_name, value in DEFAULTS.items():
            if data[field_name].get("source") == RequirementSource.MISSING.value:
                data[field_name] = {
                    "value": value,
                    "source": RequirementSource.DEFAULT.value,
                    "confidence": 1.0,
                    "confirmed": False,
                }

        snapshot = RequirementSnapshot.model_validate(data)
        issues = self._blocking_issues(snapshot, conflicts)
        if extraction_failed:
            issues.insert(
                0,
                BlockingIssue(
                    code="extraction_failed",
                    fields=["destination"],
                    message="本轮消息未能稳定解析，需要用户重新表述。",
                ),
            )
        questions = self._questions(issues)
        return RequirementDecision(
            snapshot=snapshot,
            conflicts=conflicts,
            blocking_issues=issues,
            questions=questions,
            ready=not issues,
        )

    def _blocking_issues(
        self,
        snapshot: RequirementSnapshot,
        conflicts: list[RequirementConflict],
    ) -> list[BlockingIssue]:
        issues: list[BlockingIssue] = []
        if not snapshot.origin.value:
            issues.append(self._issue("missing_origin", "origin", "请确认从哪里出发。"))
        if snapshot.start_date.value is None:
            issues.append(
                self._issue("missing_start_date", "start_date", "请确认行程开始日期。")
            )
        elif snapshot.start_date.value < self.today:
            issues.append(
                self._issue("invalid_start_date", "start_date", "开始日期不能早于今天。")
            )
        if snapshot.days.value is None:
            issues.append(self._issue("missing_days", "days", "请确认旅行天数。"))
        elif not 1 <= snapshot.days.value <= 7:
            issues.append(self._issue("invalid_days", "days", "旅行天数必须为 1～7 天。"))
        if snapshot.transport_mode.value not in {"driving", "walking"}:
            issues.append(
                self._issue(
                    "missing_transport_mode",
                    "transport_mode",
                    "请选择驾车或步行作为主要交通方式。",
                )
            )
        target_values = [
            snapshot.destination.value,
            *(snapshot.must_visit_place_texts.value or []),
            *(snapshot.optional_place_texts.value or []),
        ]
        if not any(target_values):
            issues.append(
                self._issue(
                    "missing_target",
                    "destination",
                    "请提供目的地城市或至少一个想去的地点。",
                )
            )
        for conflict in conflicts:
            if conflict.reason == "ambiguous_input":
                issues.append(
                    BlockingIssue(
                        code="ambiguous_requirement",
                        fields=[conflict.field],
                        message=conflict.message,
                    )
                )
        return issues

    @staticmethod
    def _issue(code: BlockingCode, field: RequirementField, message: str) -> BlockingIssue:
        return BlockingIssue(code=code, fields=[field], message=message)

    @staticmethod
    def _questions(issues: list[BlockingIssue]) -> list[ClarificationQuestion]:
        questions: list[ClarificationQuestion] = []
        for issue in issues[:3]:
            digest = hashlib.sha256(
                f"{issue.code}:{','.join(issue.fields)}".encode()
            ).hexdigest()[:12]
            questions.append(
                ClarificationQuestion(
                    question_id=f"clarify-{digest}",
                    issue_code=issue.code,
                    fields=issue.fields,
                    prompt=issue.message,
                )
            )
        return questions

    @staticmethod
    def _normalize(field_name: str, value: Any) -> Any:
        if field_name in LIST_FIELDS:
            seen: set[str] = set()
            normalized: list[str] = []
            for item in value:
                cleaned = " ".join(item.split())
                if cleaned and cleaned not in seen:
                    normalized.append(cleaned)
                    seen.add(cleaned)
            return normalized
        if isinstance(value, str):
            return " ".join(value.split())
        return value

    @staticmethod
    def _apply_list_operation(
        current: object, incoming: list[str], operation: str
    ) -> list[str]:
        existing = list(current) if isinstance(current, list) else []
        if operation == "replace":
            return incoming
        if operation == "remove":
            removed = set(incoming)
            return [item for item in existing if item not in removed]
        seen = set(existing)
        return [*existing, *(item for item in incoming if item not in seen)]
