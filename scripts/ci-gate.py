#!/usr/bin/env python3
"""Fail the single CI gate unless every planned reusable workflow succeeded."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PLANNED_JOBS = {
    "hygiene": "run_hygiene",
    "backend": "run_backend",
    "e2e": "run_e2e",
    "compose": "run_compose",
    "integrity": "run_integrity",
    "security": "run_security",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--needs-file", type=Path)
    args = parser.parse_args()
    raw = (
        args.needs_file.read_text(encoding="utf-8")
        if args.needs_file
        else os.environ.get("CI_NEEDS_JSON", "")
    )
    if not raw:
        print("error: CI needs JSON is required", file=sys.stderr)
        raise SystemExit(1)
    needs = json.loads(raw)
    failures: list[str] = []

    plan = needs.get("plan", {})
    if plan.get("result") != "success":
        failures.append(f"plan={plan.get('result', 'missing')}")
        outputs: dict[str, str] = {}
    else:
        outputs = plan.get("outputs", {})

    for job, output_name in PLANNED_JOBS.items():
        expected = outputs.get(output_name) == "true"
        result = needs.get(job, {}).get("result", "missing")
        if expected and result != "success":
            failures.append(f"{job}={result} (required)")
        elif not expected and result not in {"skipped", "success"}:
            failures.append(f"{job}={result} (unexpected failure)")

    if failures:
        print("CI Gate failed:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)
    print("CI Gate passed: every planned check succeeded")


if __name__ == "__main__":
    main()
