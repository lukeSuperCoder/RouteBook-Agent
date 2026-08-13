from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from services.api.app.requirements import RequirementPatch, RequirementService
from services.api.app.requirements.extractor import AnthropicRequirementExtractor
from services.api.app.schemas import RequirementSnapshot

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "docs" / "evaluation" / "phase3-requirements.json"


def evaluate(path: Path) -> tuple[int, int, float]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    passed = 0
    cases: list[dict[str, Any]] = payload["cases"]
    for case in cases:
        decision = RequirementService(today=date.fromisoformat(case["today"])).apply(
            RequirementSnapshot(), RequirementPatch.model_validate(case["patch"])
        )
        actual = {
            "ready": decision.ready,
            "issue_codes": [issue.code for issue in decision.blocking_issues],
            "conflict_reasons": [conflict.reason for conflict in decision.conflicts],
        }
        if actual == case["expect"]:
            passed += 1
        else:
            print(f"FAIL {case['id']}: expected={case['expect']} actual={actual}")
    score = passed / len(cases) if cases else 0.0
    return passed, len(cases), score


def evaluate_live(path: Path) -> tuple[int, int, float]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    extractor = AnthropicRequirementExtractor()
    passed = 0
    total_fields = 0
    for case in payload["cases"]:
        expected_patch = RequirementPatch.model_validate(case["patch"])
        result = extractor.extract(
            case["message"],
            RequirementSnapshot(),
            today=date.fromisoformat(case["today"]),
        )
        expected = expected_patch.model_dump(mode="json", exclude_none=True)
        actual = result.patch.model_dump(mode="json", exclude_none=True)
        for field, expected_value in expected.items():
            if field == "ambiguities":
                continue
            if case["kind"] == "pollution" and field.endswith("_place_texts"):
                continue
            total_fields += 1
            actual_value = actual.get(field)
            expected_semantic = _semantic_patch_value(expected_value)
            actual_semantic = _semantic_patch_value(actual_value)
            if actual_semantic == expected_semantic:
                passed += 1
            else:
                print(
                    f"LIVE MISS {case['id']}.{field}: "
                    f"expected={expected_semantic} actual={actual_semantic}"
                )
        expected_ambiguities = expected.get("ambiguities", [])
        if expected_ambiguities:
            total_fields += 1
            expected_fields = {item["field"] for item in expected_ambiguities}
            actual_fields = {item["field"] for item in actual.get("ambiguities", [])}
            if expected_fields <= actual_fields:
                passed += 1
            else:
                print(
                    f"LIVE MISS {case['id']}.ambiguities: "
                    f"expected_fields={expected_fields} actual_fields={actual_fields}"
                )
        if case["kind"] == "pollution":
            total_fields += 1
            polluted = any(
                getattr(result.patch, field) is not None
                for field in (
                    "must_visit_place_texts",
                    "optional_place_texts",
                    "excluded_place_texts",
                    "visited_place_texts",
                )
            )
            if not polluted:
                passed += 1
            else:
                print(f"LIVE MISS {case['id']}: model recommendation polluted requirements")
    score = passed / total_fields if total_fields else 0.0
    return passed, total_fields, score


def _semantic_patch_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {
        key: item
        for key, item in value.items()
        if key in {"value", "source", "operation"}
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    passed, total, score = (
        evaluate_live(args.dataset) if args.live else evaluate(args.dataset)
    )
    threshold = 0.90 if args.live else float(payload["threshold"])
    mode = "live model" if args.live else "replay"
    print(
        f"phase3 requirement {mode}: {passed}/{total} "
        f"score={score:.3f} threshold={threshold:.3f}"
    )
    return 0 if score >= threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
