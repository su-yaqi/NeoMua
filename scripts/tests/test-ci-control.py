from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "scripts" / "ci-plan.py"
GATE = ROOT / "scripts" / "ci-gate.py"


def pr_body(
    risk: str,
    context: str = "none",
    *,
    complete_h: bool = False,
) -> str:
    risks = {key: "x" if key == risk else " " for key in ("S", "M", "H")}
    contexts = {key: "x" if key == context else " " for key in ("none", "updated")}
    mark = "x" if complete_h else " "
    return f"""## Change risk
- [{risks["S"]}] S — low-risk adjustment with no product, data, permission, or topology change
- [{risks["M"]}] M — normal feature, bug fix, or local behavior/process change
- [{risks["H"]}] H — auth, permissions, database/migration, dependencies, deployment, runtime topology, or cross-module architecture

Risk rationale: correct level

## Validation
- Test evidence: test command passed

### H-only gates
- [{mark}] Full relevant test suite passed
- [{mark}] Migration or data-change validation completed, or a concrete non-applicability reason recorded
- [{mark}] Rollback/recovery validation completed
- [{mark}] Staging health check and critical smoke flow passed

- Full tests: complete
- Migration/data: not applicable because no schema changed
- Rollback/recovery: workflow rollback inspected
- Staging: staging smoke passed

## Context impact
- [{contexts["none"]}] `none` — no semantic capability/current-fact change
- [{contexts["updated"]}] `updated` — semantic capability/current facts changed

Reason: context selection is accurate
"""


class PlanTests(unittest.TestCase):
    def run_plan(
        self,
        *,
        event_name: str = "pull_request",
        body: str = "",
        files: tuple[str, ...] = ("README.md",),
    ) -> subprocess.CompletedProcess[str]:
        event = {
            "before": "before-sha",
            "after": "after-sha",
            "pull_request": {
                "body": body,
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha"},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            event_file = tmp / "event.json"
            files_file = tmp / "files.txt"
            event_file.write_text(json.dumps(event), encoding="utf-8")
            files_file.write_text("\n".join(files) + "\n", encoding="utf-8")
            return subprocess.run(
                [
                    str(PLAN),
                    "--event-file",
                    str(event_file),
                    "--event-name",
                    event_name,
                    "--files-file",
                    str(files_file),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_docs_s_runs_only_hygiene(self) -> None:
        result = self.run_plan(body=pr_body("S"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("run_hygiene=true", result.stdout)
        self.assertIn("run_e2e=false", result.stdout)

    def test_backend_m_runs_affected_checks(self) -> None:
        result = self.run_plan(body=pr_body("M"), files=("backend/app/main.py",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("run_backend=true", result.stdout)
        self.assertIn("run_e2e=true", result.stdout)
        self.assertIn("run_integrity=true", result.stdout)

    def test_ci_controller_change_runs_integrity(self) -> None:
        result = self.run_plan(body=pr_body("M"), files=("scripts/ci-gate.py",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("run_integrity=true", result.stdout)

    def test_workflow_h_runs_everything(self) -> None:
        result = self.run_plan(
            body=pr_body("H", complete_h=True),
            files=(".github/workflows/ci.yml",),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("full=true", result.stdout)
        self.assertIn("run_security=true", result.stdout)

    def test_h_rejects_failed_evidence(self) -> None:
        result = self.run_plan(
            body=pr_body("H", complete_h=True).replace(
                "- Full tests: complete",
                "- Full tests: coverage gate failed",
            ),
            files=(".github/workflows/ci.yml",),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("successful evidence", result.stderr)

    def test_non_pr_is_full(self) -> None:
        result = self.run_plan(event_name="push")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("risk=H", result.stdout)
        self.assertIn("run_compose=true", result.stdout)

    def test_understated_risk_fails(self) -> None:
        result = self.run_plan(
            body=pr_body("S"),
            files=(".github/workflows/ci.yml",),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("below path-based minimum", result.stderr)

    def test_missing_risk_selection_fails(self) -> None:
        result = self.run_plan(body=pr_body("S").replace("[x] S", "[ ] S"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly one risk level", result.stderr)


class GateTests(unittest.TestCase):
    def run_gate(self, needs: dict[str, object]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["CI_NEEDS_JSON"] = json.dumps(needs)
        return subprocess.run(
            [str(GATE)],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def needs(*, backend: bool = False, backend_result: str = "skipped") -> dict:
        outputs = {
            "run_hygiene": "true",
            "run_backend": str(backend).lower(),
            "run_e2e": "false",
            "run_compose": "false",
            "run_integrity": "false",
            "run_security": "false",
        }
        return {
            "plan": {"result": "success", "outputs": outputs},
            "hygiene": {"result": "success"},
            "backend": {"result": backend_result},
            "e2e": {"result": "skipped"},
            "compose": {"result": "skipped"},
            "integrity": {"result": "skipped"},
            "security": {"result": "skipped"},
        }

    def test_gate_accepts_unplanned_skips(self) -> None:
        result = self.run_gate(self.needs())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_accepts_successful_planned_job(self) -> None:
        result = self.run_gate(self.needs(backend=True, backend_result="success"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_rejects_failed_planned_job(self) -> None:
        result = self.run_gate(self.needs(backend=True, backend_result="failure"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backend=failure", result.stderr)


class WorkflowPolicyTests(unittest.TestCase):
    def test_backend_coverage_is_reported_without_a_threshold(self) -> None:
        backend = (ROOT / ".github/workflows/test-backend.yml").read_text(
            encoding="utf-8"
        )
        smokeshow = (ROOT / ".github/workflows/smokeshow.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("make DEV_SLOT=90 test-backend", backend)
        self.assertIn("name: coverage-html", backend)
        self.assertNotIn("COVERAGE_FAIL_UNDER=90", backend)
        self.assertIn("SMOKESHOW_GITHUB_COVERAGE_THRESHOLD: 0", smokeshow)

    def test_client_generation_includes_the_local_test_api(self) -> None:
        generator = (ROOT / "scripts/generate-client.sh").read_text(encoding="utf-8")
        self.assertIn(
            "ENABLE_PRIVATE_TEST_API=true uv run python",
            generator,
        )


if __name__ == "__main__":
    unittest.main()
