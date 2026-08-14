from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Gate:
    name: str
    command: tuple[str, ...]
    cwd: Path = ROOT


GATES = (
    Gate("ruff", ("uv", "run", "ruff", "check", ".")),
    Gate("mypy", ("uv", "run", "mypy", "services")),
    Gate("contracts", ("uv", "run", "python", "-m", "scripts.export_contracts", "--check")),
    Gate("prompt replay", ("uv", "run", "python", "-m", "scripts.evaluate_phase3_requirements")),
    Gate(
        "backend tests",
        ("uv", "run", "pytest", "-q", "-m", "not integration and not provider_live"),
    ),
    Gate("frontend lint", ("pnpm", "lint"), ROOT / "apps/web"),
    Gate("frontend types", ("pnpm", "typecheck"), ROOT / "apps/web"),
    Gate("frontend tests", ("pnpm", "test"), ROOT / "apps/web"),
    Gate("frontend build", ("pnpm", "build"), ROOT / "apps/web"),
    Gate("compose", ("docker", "compose", "config", "--quiet")),
)

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|secret|password)\s*=\s*['\"]?[A-Za-z0-9_\-/+=]{16,}"),
)
SKIP_PARTS = {".git", ".next", ".venv", "node_modules", "__pycache__", "tests"}


def scan_tracked_files() -> list[str]:
    tracked = subprocess.run(
        ("git", "ls-files", "-z"), cwd=ROOT, check=True, capture_output=True
    ).stdout.split(b"\0")
    findings: list[str] = []
    for raw_path in tracked:
        if not raw_path:
            continue
        path = ROOT / os.fsdecode(raw_path)
        if any(part in SKIP_PARTS for part in path.parts) or not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        contains_secret = any(pattern.search(content) for pattern in SECRET_PATTERNS)
        if path.name != ".env.example" and contains_secret:
            findings.append(str(path.relative_to(ROOT)))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic Phase 8 release gates.")
    parser.add_argument(
        "--skip-build", action="store_true", help="Skip the Next.js production build."
    )
    args = parser.parse_args()
    findings = scan_tracked_files()
    if findings:
        print("[FAIL] credential scan: " + ", ".join(findings))
        return 1
    print("[PASS] credential scan")
    for gate in GATES:
        if args.skip_build and gate.name == "frontend build":
            continue
        print(f"[RUN] {gate.name}", flush=True)
        result = subprocess.run(gate.command, cwd=gate.cwd, check=False)
        if result.returncode:
            print(f"[FAIL] {gate.name}")
            return result.returncode
        print(f"[PASS] {gate.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
