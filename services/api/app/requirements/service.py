from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, cast

from ..enums import RequirementSource
from ..schemas import RequirementSnapshot
from .models import (
    BlockingCode,
    BlockingIssue,
    ClarificationOption,
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
DEFAULT_REASONS: dict[str, str] = {
    "companions": "未识别到特殊同行约束，先按普通成人同行处理。",
    "themes": "暂不限制主题，让候选地点保持多样性。",
    "intensity": "中等节奏通常能兼顾游览完整度和休息时间。",
    "crowd_tolerance": "采用中等人流容忍度，避免过度排除热门地标。",
    "suburban_acceptance": "默认不安排远郊，减少通勤对短途行程的挤压。",
}
QUESTION_PRIORITY: dict[str, tuple[int, float, str]] = {
    "extraction_failed": (100, 1.0, "需要先获得可稳定解析的旅行目标。"),
    "missing_target": (98, 1.0, "目的地决定后续搜索范围和数据源调用。"),
    "ambiguous_requirement": (95, 0.95, "消除歧义可避免覆盖已经确认的条件。"),
    "missing_trip_scope": (90, 0.9, "是否包含往返会改变出发地和交通问题。"),
    "missing_days": (82, 0.85, "天数直接决定候选数量和每日容量。"),
    "invalid_days": (82, 0.85, "有效天数是路线编排的硬约束。"),
    "missing_start_date": (72, 0.7, "日期影响天气和营业信息，也可以暂时跳过。"),
    "invalid_start_date": (72, 0.7, "需要有效日期才能查询对应事实。"),
    "missing_origin": (68, 0.65, "仅在包含往返时用于计算跨城交通。"),
    "missing_transport_mode": (60, 0.6, "交通方式会影响地点间距和每日容量。"),
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
            if incoming.source == RequirementSource.INFERRED and (
                current_value.get("confirmed") or current_value.get("source") == "explicit"
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
                "suggestion_reason": None,
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
                    "suggestion_reason": DEFAULT_REASONS.get(field_name),
                }

        # Backward-compatible derivation for snapshots created before stage B.
        if data["trip_scope"].get("source") == RequirementSource.MISSING.value and data[
            "origin"
        ].get("value"):
            data["trip_scope"] = {
                "value": "door_to_door",
                "source": RequirementSource.INFERRED.value,
                "confidence": 1.0,
                "confirmed": False,
                "decision_status": "suggested",
            }
        if data["date_precision"].get("source") == RequirementSource.MISSING.value and data[
            "start_date"
        ].get("value"):
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
        questions = self._questions(issues, snapshot)
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
        if (
            snapshot.date_precision.value not in {"month_only", "flexible"}
            and snapshot.start_date.value is None
        ):
            issues.append(
                self._issue(
                    "missing_start_date", "start_date", "请确认具体日期，或选择日期暂未确定。"
                )
            )
        elif snapshot.start_date.value is not None and snapshot.start_date.value < self.today:
            issues.append(self._issue("invalid_start_date", "start_date", "开始日期不能早于今天。"))
        if snapshot.days.value is None:
            issues.append(self._issue("missing_days", "days", "请确认旅行天数。"))
        elif not 1 <= snapshot.days.value <= 7:
            issues.append(self._issue("invalid_days", "days", "旅行天数必须为 1～7 天。"))
        if snapshot.transport_mode.value not in {
            "driving",
            "walking",
            "public_transit",
            "taxi",
            "cycling",
            "mixed",
            "system_decides",
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
    def _questions(
        issues: list[BlockingIssue], snapshot: RequirementSnapshot
    ) -> list[ClarificationQuestion]:
        questions: list[ClarificationQuestion] = []
        ranked = sorted(
            enumerate(issues),
            key=lambda item: (
                -QUESTION_PRIORITY.get(item[1].code, (50, 0.5, ""))[0],
                item[0],
            ),
        )
        for _, issue in ranked[:3]:
            digest = hashlib.sha256(f"{issue.code}:{','.join(issue.fields)}".encode()).hexdigest()[
                :12
            ]
            input_type = "text"
            options: list[ClarificationOption] = []
            allow_skip = False
            skip_label = None
            priority, information_gain, rationale = QUESTION_PRIORITY.get(
                issue.code, (50, 0.5, "确认后可以继续规划。")
            )
            recommended_option_value = None
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
            elif issue.code in {"missing_days", "invalid_days"}:
                input_type = "single_choice"
                options = [
                    ClarificationOption(value=f"安排 {days} 天", label=f"{days} 天")
                    for days in (2, 3, 5)
                ]
                recommended_option_value = "安排 3 天"
            elif issue.code == "missing_transport_mode":
                input_type = "single_choice"
                companions = " ".join(snapshot.companions.value or [])
                mobility_sensitive = any(
                    token in companions for token in ("老人", "长者", "儿童", "行动不便", "轮椅")
                )
                recommended_option_value = (
                    "打车为主"
                    if mobility_sensitive
                    else (
                        "地铁和步行为主"
                        if snapshot.trip_scope.value == "destination_only"
                        else "由你根据路线安排交通方式"
                    )
                )
                options = [
                    ClarificationOption(
                        value="地铁和步行为主",
                        label="公共交通为主",
                        description="适合路网完善、地点集中的城市行程",
                    ),
                    ClarificationOption(
                        value="打车为主",
                        label="打车为主",
                        description="减少步行和换乘，成本相对更高",
                    ),
                    ClarificationOption(value="自驾", label="自驾"),
                    ClarificationOption(value="混合交通，按当天路线切换", label="混合交通"),
                    ClarificationOption(
                        value="由你根据路线安排交通方式",
                        label="由系统安排",
                        description="按距离、同行人和路线密度自动选择",
                    ),
                ]
            options = [
                option.model_copy(
                    update={
                        "recommended": option.value == recommended_option_value,
                        "recommendation_reason": (
                            "结合当前已知条件，这是更稳妥的起点。"
                            if option.value == recommended_option_value
                            else None
                        ),
                    }
                )
                for option in options
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
                    priority=priority,
                    information_gain=information_gain,
                    rationale=rationale,
                    recommended_option_value=recommended_option_value,
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
    def _apply_list_operation(current: object, incoming: list[str], operation: str) -> list[str]:
        existing = list(current) if isinstance(current, list) else []
        if operation == "replace":
            return incoming
        if operation == "remove":
            removed = set(incoming)
            return [item for item in existing if item not in removed]
        seen = set(existing)
        return [*existing, *(item for item in incoming if item not in seen)]
