from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any
from uuid import UUID

from ..schemas import (
    ItineraryDaySnapshot,
    RouteBookSnapshotV1,
)
from .models import EditIntent, EditPlan, ImpactScope, ReferenceResolution, RiskFlag

CHINESE_DAY_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}


class EditingService:
    def plan(self, snapshot: RouteBookSnapshotV1, intent: EditIntent) -> EditPlan:
        resolution = self.resolve_references(snapshot, intent)
        if not resolution.resolved:
            return EditPlan(
                resolution=resolution,
                impact=ImpactScope(),
                change_summary="编辑引用需要澄清",
            )
        impact, risks = self.calculate_impact(snapshot, intent, resolution)
        preview = self.build_preview(snapshot, intent, resolution, impact)
        return EditPlan(
            resolution=resolution,
            impact=impact,
            preview=preview,
            risks=risks,
            change_summary=self._summary(intent, resolution),
        )

    def resolve_references(
        self, snapshot: RouteBookSnapshotV1, intent: EditIntent
    ) -> ReferenceResolution:
        days: list[int] = []
        if intent.day_reference:
            day = self._parse_day(intent.day_reference)
            if day is None or day not in {item.day_number for item in snapshot.days_plan}:
                return ReferenceResolution(
                    resolved=False,
                    clarification=f"无法唯一解析“{intent.day_reference}”，请指定第几天。",
                    candidates=[f"第{item.day_number}天" for item in snapshot.days_plan],
                )
            days = [day]
        place_ids: list[UUID] = []
        if intent.place_reference:
            reference = intent.place_reference.strip()
            matches = [
                place
                for place in snapshot.places
                if reference in place.name or str(place.id) == reference
            ]
            if len(matches) != 1:
                return ReferenceResolution(
                    resolved=False,
                    clarification=f"无法唯一解析地点“{reference}”，请选择具体地点。",
                    candidates=[f"{item.name}（{item.district}）" for item in matches]
                    or [item.name for item in snapshot.places],
                )
            place_ids = [matches[0].id]
            matched_days = [
                day.day_number for day in snapshot.days_plan if matches[0].id in day.place_ids
            ]
            if days and matched_days and days[0] not in matched_days:
                return ReferenceResolution(
                    resolved=False,
                    clarification="指定地点不在目标日期中，请重新选择。",
                    candidates=[f"第{item}天" for item in matched_days],
                )
            days = days or matched_days
        if intent.operation == "change_days":
            days = [item.day_number for item in snapshot.days_plan]
        return ReferenceResolution(resolved=True, day_numbers=days, place_ids=place_ids)

    def calculate_impact(
        self,
        snapshot: RouteBookSnapshotV1,
        intent: EditIntent,
        resolution: ReferenceResolution,
    ) -> tuple[ImpactScope, list[RiskFlag]]:
        affected_days = resolution.day_numbers.copy()
        if intent.operation == "change_days" and intent.target_days:
            affected_days = list(
                range(
                    1,
                    max(len(snapshot.days_plan), intent.target_days) + 1,
                )
            )
        if intent.operation == "add_place" and not affected_days:
            affected_days = [snapshot.days_plan[-1].day_number] if snapshot.days_plan else [1]
        affected_place_ids = resolution.place_ids.copy()
        if intent.replacement_place:
            affected_place_ids.append(intent.replacement_place.id)
        affected_day_segment_ids = {
            segment_id
            for day in snapshot.days_plan
            if day.day_number in affected_days
            for segment_id in day.segment_ids
        }
        affected_segments = [
            segment.id
            for segment in snapshot.route_segments
            if segment.id in affected_day_segment_ids
            or segment.origin_place_id in resolution.place_ids
            or segment.destination_place_id in resolution.place_ids
        ]
        risks: list[RiskFlag] = []
        must_ids = set(snapshot.requirements.must_visit_place_ids.value or [])
        if intent.operation in {"remove_place", "replace_place"} and must_ids.intersection(
            resolution.place_ids
        ):
            risks.append(
                RiskFlag(
                    code="remove_must_visit",
                    message="修改会删除或替换用户必去地点。",
                )
            )
        if len(affected_days) > 1:
            risks.append(RiskFlag(code="multi_day_change", message="修改会影响多个行程日。"))
        if intent.operation == "change_days":
            risks.append(RiskFlag(code="change_total_days", message="修改会改变总行程天数。"))
        major_route = intent.operation == "change_days" or len(affected_days) > 1
        if major_route:
            risks.append(RiskFlag(code="major_route_change", message="修改会改变主要路线结构。"))
        return (
            ImpactScope(
                affected_days=sorted(set(affected_days)),
                affected_place_ids=list(dict.fromkeys(affected_place_ids)),
                affected_segment_ids=affected_segments,
                weather_locations_changed=intent.operation
                in {"add_place", "remove_place", "replace_place", "change_days"},
                major_route_changed=major_route,
                requires_confirmation=bool(risks),
            ),
            risks,
        )

    def build_preview(
        self,
        snapshot: RouteBookSnapshotV1,
        intent: EditIntent,
        resolution: ReferenceResolution,
        impact: ImpactScope,
    ) -> RouteBookSnapshotV1:
        data = deepcopy(snapshot.model_dump(mode="python"))
        places = {item["id"]: item for item in data["places"]}
        days = data["days_plan"]
        target_day = impact.affected_days[0] if impact.affected_days else 1
        if intent.operation == "add_place" and intent.replacement_place:
            places[intent.replacement_place.id] = intent.replacement_place.model_dump(mode="python")
            self._day(days, target_day)["place_ids"].append(intent.replacement_place.id)
        elif intent.operation in {"remove_place", "replace_place"}:
            old_id = resolution.place_ids[0]
            for day in days:
                day["place_ids"] = [item for item in day["place_ids"] if item != old_id]
            places.pop(old_id, None)
            if intent.operation == "replace_place" and intent.replacement_place:
                places[intent.replacement_place.id] = intent.replacement_place.model_dump(
                    mode="python"
                )
                self._day(days, target_day)["place_ids"].append(intent.replacement_place.id)
        elif intent.operation == "edit_day":
            target = self._day(days, target_day)
            target["place_ids"] = self._nearest_place_order(target["place_ids"], places)
            target["segment_ids"] = []
            target["weather_refs"] = []
        elif intent.operation == "change_days" and intent.target_days:
            days[:] = days[: intent.target_days]
            while len(days) < intent.target_days:
                number = len(days) + 1
                days.append(ItineraryDaySnapshot(day_number=number).model_dump(mode="python"))
            data["requirements"]["days"]["value"] = intent.target_days
        data["places"] = list(places.values())
        if intent.operation in {"add_place", "remove_place", "replace_place", "change_days", "edit_day"}:
            affected = set(impact.affected_days)
            affected_place_ids = {
                place_id
                for day in days
                if day["day_number"] in affected
                for place_id in day["place_ids"]
            }
            data["route_segments"] = [
                item
                for item in data["route_segments"]
                if item["id"] not in impact.affected_segment_ids
            ]
            data["weather"] = [
                item for item in data["weather"] if item["place_id"] not in affected_place_ids
            ]
            for day in days:
                if day["day_number"] in affected:
                    day["segment_ids"] = []
                    day["weather_refs"] = []
        return RouteBookSnapshotV1.model_validate(data)

    @staticmethod
    def validate_unchanged_days(
        base: RouteBookSnapshotV1,
        preview: RouteBookSnapshotV1,
        affected_days: list[int],
    ) -> bool:
        affected = set(affected_days)
        base_days = {day.day_number: day for day in base.days_plan}
        preview_days = {day.day_number: day for day in preview.days_plan}
        for number in set(base_days).union(preview_days) - affected:
            if day_hash(base, base_days.get(number)) != day_hash(preview, preview_days.get(number)):
                return False
        return True

    @staticmethod
    def _parse_day(value: str) -> int | None:
        normalized = value.strip()
        if normalized in {"这个日期", "这一天", "当天"}:
            return None
        match = re.search(r"第?([1-7一二三四五六七])天", normalized)
        if not match:
            return None
        raw = match.group(1)
        return int(raw) if raw.isdigit() else CHINESE_DAY_NUMBERS[raw]

    @staticmethod
    def _day(days: list[dict[str, Any]], number: int) -> dict[str, Any]:
        return next(item for item in days if item["day_number"] == number)

    @staticmethod
    def _nearest_place_order(place_ids: list[UUID], places: dict[UUID, dict[str, Any]]) -> list[UUID]:
        if len(place_ids) < 3:
            return place_ids.copy()
        remaining = list(place_ids[1:])
        ordered = [place_ids[0]]
        while remaining:
            origin = places[ordered[-1]]
            next_id = min(
                remaining,
                key=lambda place_id: (
                    (places[place_id]["longitude"] - origin["longitude"]) ** 2
                    + (places[place_id]["latitude"] - origin["latitude"]) ** 2,
                    str(place_id),
                ),
            )
            ordered.append(next_id)
            remaining.remove(next_id)
        return ordered

    @staticmethod
    def _summary(intent: EditIntent, resolution: ReferenceResolution) -> str:
        target = intent.place_reference or intent.day_reference or str(intent.target_days or "")
        return f"{intent.operation}: {target}".strip()


def day_hash(snapshot: RouteBookSnapshotV1, day: ItineraryDaySnapshot | None) -> str | None:
    if day is None:
        return None
    segment_ids = set(day.segment_ids)
    weather_refs = set(day.weather_refs)
    payload = {
        "day": day.model_dump(mode="json"),
        "segments": [
            item.model_dump(mode="json")
            for item in snapshot.route_segments
            if item.id in segment_ids
        ],
        "weather": [
            item.model_dump(mode="json") for item in snapshot.weather if item.ref in weather_refs
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
