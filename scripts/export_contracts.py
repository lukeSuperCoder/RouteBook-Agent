from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.api.app.main import create_app
from services.api.app.schemas import RouteBookSnapshotV1

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "docs" / "contracts"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def generated_contracts() -> dict[Path, str]:
    app = create_app(lambda _run_id, _request_id: None)
    return {
        CONTRACTS / "openapi.json": canonical_json(app.openapi()),
        CONTRACTS / "routebook-snapshot-v1.schema.json": canonical_json(
            RouteBookSnapshotV1.model_json_schema()
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    contracts = generated_contracts()
    if args.check:
        stale = [
            path
            for path, content in contracts.items()
            if not path.exists() or path.read_text() != content
        ]
        if stale:
            print("Contract files are stale:")
            for path in stale:
                print(f"- {path.relative_to(ROOT)}")
            return 1
        return 0
    CONTRACTS.mkdir(parents=True, exist_ok=True)
    for path, content in contracts.items():
        path.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
