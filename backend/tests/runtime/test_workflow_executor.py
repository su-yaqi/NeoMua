import subprocess
from pathlib import Path

import pytest
from workflow_runtime.executor import execute_runtime_job


def _git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _git(workspace, "init")
    _git(workspace, "config", "user.email", "runtime@example.test")
    _git(workspace, "config", "user.name", "Runtime Probe")
    specs = workspace / "context/specs"
    specs.mkdir(parents=True)
    (specs / "v06.md").write_text("# v0.6\n可审计规范", encoding="utf-8")
    _git(workspace, "add", "context/specs/v06.md")
    _git(workspace, "commit", "-m", "add spec")
    _git(workspace, "branch", "-M", "main")
    _git(workspace, "remote", "add", "origin", str(remote))
    _git(workspace, "push", "-u", "origin", "main")
    return workspace, remote


def test_repository_probe_verifies_git_and_materializes_spec_content(
    tmp_path: Path,
) -> None:
    workspace, remote = _repository(tmp_path)
    result = execute_runtime_job(
        "repository_probe",
        {
            "workspace_ref": "workspace://project",
            "workspace_path": str(workspace),
            "allowed_roots": [str(tmp_path)],
            "remote_url": str(remote),
            "default_branch": "main",
            "spec_locations": [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "path": "context/specs",
                    "location_type": "directory",
                }
            ],
        },
    )
    assert len(result["commit"]) == 40
    assert result["remote_url"] == str(remote)
    assert result["spec_locations"][0]["files"][0]["content"] == ("# v0.6\n可审计规范")


def test_repository_probe_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace, remote = _repository(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (workspace / "escape.md").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes the repository"):
        execute_runtime_job(
            "repository_probe",
            {
                "workspace_ref": "workspace://project",
                "workspace_path": str(workspace),
                "allowed_roots": [str(tmp_path)],
                "remote_url": str(remote),
                "spec_locations": [
                    {
                        "id": "00000000-0000-0000-0000-000000000002",
                        "path": "escape.md",
                        "location_type": "file",
                    }
                ],
            },
        )
