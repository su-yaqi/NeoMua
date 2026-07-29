#!/usr/bin/env python3
"""Plan graded CI jobs from a GitHub event, PR declaration, and changed paths."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn


ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check-change-process.sh"
RISK_LABELS = {
    "S": "S — low-risk adjustment",
    "M": "M — normal feature",
    "H": "H — auth, permissions",
}
CONTEXT_LABELS = {
    "none": "`none` — no semantic",
    "updated": "`updated` — semantic",
}


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def checked_value(body: str, labels: dict[str, str], name: str) -> str:
    selected = [
        key
        for key, marker in labels.items()
        if re.search(rf"(?mi)^-\s*\[[xX]\]\s*{re.escape(marker)}", body)
    ]
    if len(selected) != 1:
        fail(f"PR body must select exactly one {name}; selected={selected or 'none'}")
    return selected[0]


def field(body: str, label: str) -> str:
    match = re.search(rf"(?mi)^{re.escape(label)}\s*(.+?)\s*$", body)
    if not match:
        fail(f"PR body field is missing or empty: {label}")
    return match.group(1).strip()


def h_checkbox(body: str, label: str) -> None:
    if not re.search(rf"(?mi)^-\s*\[[xX]\]\s*{re.escape(label)}\s*$", body):
        fail(f"H PR must check: {label}")


def path_flags(paths: list[str], risk: str) -> dict[str, bool]:
    flags = {
        "run_hygiene": True,
        "run_backend": False,
        "run_e2e": False,
        "run_compose": False,
        "run_integrity": False,
        "run_security": False,
    }
    if risk == "H":
        return {key: True for key in flags}

    for path in paths:
        if path.startswith(
            (
                "backend/",
                "runtime_worker/",
                "workflow_runtime/",
                "model_gateway/",
                "node_runtime/",
                "operator_cli/",
            )
        ):
            flags["run_backend"] = True
            flags["run_e2e"] = True
            flags["run_integrity"] = True
        if path.startswith(("frontend/", "workflow_apps/")):
            flags["run_e2e"] = True
            flags["run_integrity"] = True
        if path.startswith(".github/workflows/") or path.startswith(".github/actions/"):
            flags["run_integrity"] = True
            flags["run_security"] = True
        if (
            path
            in {
                ".github/PULL_REQUEST_TEMPLATE.md",
                "development-process.md",
                "development-ci.md",
                "scripts/check-change-process.sh",
                "scripts/ci-plan.py",
                "scripts/ci-gate.py",
            }
            or path.startswith("scripts/tests/test-ci-")
            or path.startswith("scripts/tests/test-check-change-")
        ):
            flags["run_integrity"] = True
        if (
            path in {"compose.yml", "compose.override.yml", "compose.dev-test.yml"}
            or path.startswith("compose.")
            or path == "Dockerfile"
            or "/Dockerfile" in path
            or path in {".env.example", "Makefile"}
            or path.startswith("scripts/dev-stack")
        ):
            flags["run_compose"] = True
            flags["run_integrity"] = True

    # A test-only S change runs its own suite without expanding to unrelated suites.
    if risk == "S":
        backend_only = paths and all(
            path.startswith("backend/tests/") for path in paths
        )
        frontend_only = paths and all(
            path.startswith(("frontend/tests/", "frontend/e2e/", "tests/"))
            for path in paths
        )
        if backend_only:
            flags["run_e2e"] = False
            flags["run_integrity"] = False
        if frontend_only:
            flags["run_integrity"] = False
    return flags


def write_outputs(
    output_path: Path | None, values: dict[str, str | bool | int]
) -> None:
    lines = [
        f"{key}={str(value).lower() if isinstance(value, bool) else value}"
        for key, value in values.items()
    ]
    rendered = "\n".join(lines) + "\n"
    if output_path:
        with output_path.open("a", encoding="utf-8") as output:
            output.write(rendered)
    print(rendered, end="")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-file", type=Path, required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--files-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    event = json.loads(args.event_file.read_text(encoding="utf-8"))
    is_pr = args.event_name == "pull_request"
    paths: list[str] = []
    if args.files_file:
        paths = sorted(
            {
                line.strip()
                for line in args.files_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        )
    if is_pr and not paths:
        fail("pull request planning requires at least one changed file")

    if not is_pr:
        values: dict[str, str | bool | int] = {
            "risk": "H",
            "full": True,
            "run_hygiene": True,
            "run_backend": True,
            "run_e2e": True,
            "run_compose": True,
            "run_integrity": True,
            "run_security": True,
            "base_sha": event.get("before", ""),
            "head_sha": event.get("after")
            or event.get("release", {}).get("target_commitish", ""),
            "changed_count": len(paths),
        }
        write_outputs(args.output, values)
        return

    pull_request = event.get("pull_request") or {}
    body = pull_request.get("body") or ""
    risk = checked_value(body, RISK_LABELS, "risk level")
    context_impact = checked_value(body, CONTEXT_LABELS, "context impact")
    field(body, "Risk rationale:")
    evidence = {
        "CHANGE_RISK": risk,
        "CHANGE_CONTEXT_IMPACT": context_impact,
        "CHANGE_CONTEXT_NOTE": field(body, "Reason:"),
        "CHANGE_TEST_EVIDENCE": field(body, "- Test evidence:"),
    }
    if risk == "H":
        h_fields = {
            "CHANGE_FULL_TEST_EVIDENCE": (
                "Full relevant test suite passed",
                "- Full tests:",
            ),
            "CHANGE_MIGRATION_EVIDENCE": (
                "Migration or data-change validation completed, or a concrete "
                "non-applicability reason recorded",
                "- Migration/data:",
            ),
            "CHANGE_ROLLBACK_EVIDENCE": (
                "Rollback/recovery validation completed",
                "- Rollback/recovery:",
            ),
            "CHANGE_STAGING_EVIDENCE": (
                "Staging health check and critical smoke flow passed",
                "- Staging:",
            ),
        }
        for env_name, (checkbox, body_field) in h_fields.items():
            h_checkbox(body, checkbox)
            evidence[env_name] = field(body, body_field)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as changed:
        changed.write("\n".join(paths) + "\n")
        changed.flush()
        checker_env = os.environ.copy()
        checker_env.update(evidence)
        checker_env["CHANGE_FILES_FILE"] = changed.name
        subprocess.run([str(CHECKER)], cwd=ROOT, env=checker_env, check=True)

    flags = path_flags(paths, risk)
    values = {
        "risk": risk,
        "full": risk == "H",
        **flags,
        "base_sha": pull_request.get("base", {}).get("sha", ""),
        "head_sha": pull_request.get("head", {}).get("sha", ""),
        "changed_count": len(paths),
    }
    write_outputs(args.output, values)


if __name__ == "__main__":
    main()
