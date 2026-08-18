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
    ClarificationOption,
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
                "decision_status": (
                    "confirmed" if incoming.source == RequirementSource.EXPLICIT else "suggested"
                ),
            }

        for ambiguity in patch.ambiguities:
            data[ambiguity.field]["decision_status"] = "conflicted"
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
                    "decision_status": "suggested",
                }

        # Backward-compatible derivation for snapshots created before stage B.
        if data["trip_scope"].get("source") == RequirementSource.MISSING.value and data["origin"].get("value"):
            data["trip_scope"] = {
                "value": "door_to_door",
                "source": RequirementSource.INFERRED.value,
                "confidence": 1.0,
                "confirmed": False,
                "decision_status": "suggested",
            }
        if data["date_precision"].get("source") == RequirementSource.MISSING.value and data["start_date"].get("value"):
            data["date_precision"] = {
                "value": "exact",
                "source": RequirementSource.INFERRED.value,
                "confidence": 1.0,
                "confirmed": False,
                "decision_status": "suggested",
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
        if snapshot.trip_scope.value not in {"door_to_door", "destination_only"}:
            issues.append(
                self._issue(
                    "missing_trip_scope",
                    "trip_scope",
                    "这次需要把出发地和往返交通也规划进去吗？",
                )
            )
        if snapshot.trip_scope.value == "door_to_door" and not snapshot.origin.value:
            issues.append(self._issue("missing_origin", "origin", "请确认从哪里出发。"))
        if snapshot.date_precision.value not in {"month_only", "flexible"} and snapshot.start_date.value is None:
            issues.append(
                self._issue("missing_start_date", "start_date", "请确认具体日期，或选择日期暂未确定。")
            )
        elif snapshot.start_date.value is not None and snapshot.start_date.value < self.today:
            issues.append(
                self._issue("invalid_start_date", "start_date", "开始日期不能早于今天。")
            )
        if snapshot.days.value is None:
            issues.append(self._issue("missing_days", "days", "请确认旅行天数。"))
        elif not 1 <= snapshot.days.value <= 7:
            issues.append(self._issue("invalid_days", "days", "旅行天数必须为 1～7 天。"))
        if snapshot.transport_mode.value not in {
            "driving", "walking", "public_transit", "taxi", "cycling", "mixed", "system_decides"
        }:
            issues.append(
                self._issue(
                    "missing_transport_mode",
                    "transport_mode",
                    "请选择主要交通方式，也可以交给系统按路线安排。",
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
            input_type = "text"
            options: list[ClarificationOption] = []
            allow_skip = False
            skip_label = None
            if issue.code == "missing_trip_scope":
                input_type = "single_choice"
                options = [
                    ClarificationOption(
                        value="只规划目的地内部行程，不考虑往返",
                        label="只规划目的地内部",
                        description="不需要填写出发城市",
                    ),
                    ClarificationOption(
                        value="包含往返交通，我会补充出发地",
                        label="包含出发地和往返",
                        description="下一步继续确认出发城市",
                    ),
                ]
            elif issue.code == "missing_start_date":
                input_type = "date"
                allow_skip = True
                skip_label = "日期暂未确定"
            elif issue.code == "missing_transport_mode":
                input_type = "single_choice"
                options = [
                    ClarificationOption(value="地铁和步行为主", label="公共交通为主"),
                    ClarificationOption(value="打车为主", label="打车为主"),
                    ClarificationOption(value="自驾", label="自驾"),
                    ClarificationOption(value="由你根据路线安排交通方式", label="由系统安排"),
                ]
            questions.append(
                ClarificationQuestion(
                    question_id=f"clarify-{digest}",
                    issue_code=issue.code,
                    fields=issue.fields,
                    prompt=issue.message,
                    input_type=input_type,
                    options=options,
                    allow_skip=allow_skip,
                    skip_label=skip_label,
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
