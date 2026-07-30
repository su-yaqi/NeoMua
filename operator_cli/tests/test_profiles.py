import json

import pytest
from click import Group
from typer.main import get_command
from typer.testing import CliRunner

import operator_cli.cli as cli_module
from operator_cli.cli import app
from operator_cli.profiles import Profile, ProfileStore
from operator_cli.registry import COMMANDS


def test_profile_file_contains_no_token_or_secret(tmp_path) -> None:
    store = ProfileStore(tmp_path)
    store.put(
        Profile(
            name="prod",
            api_url="https://neomua.example",
            namespace_id="00000000-0000-0000-0000-000000000001",
            output="json",
            credential_id="keychain-item-1",
        )
    )
    raw = store.path.read_text("utf-8")
    assert "token" not in raw.lower()
    assert "secret" not in raw.lower()
    assert json.loads(raw)["current"] == "prod"
    assert store.path.stat().st_mode & 0o077 == 0


def test_profile_rejects_non_https_and_credentials(tmp_path) -> None:
    store = ProfileStore(tmp_path)
    with pytest.raises(ValueError):
        store.put(Profile("bad", "http://localhost:8000", None, "human", "item"))
    with pytest.raises(ValueError):
        store.put(
            Profile("bad", "https://user:pass@example.com", None, "human", "item")
        )


def test_registry_names_are_unique() -> None:
    names = [command.name for command in COMMANDS]
    assert len(names) == len(set(names))


def test_registry_matches_the_executable_command_tree() -> None:
    root = get_command(app)
    assert isinstance(root, Group)

    def command_names(group: Group, prefix: str = "") -> set[str]:
        result: set[str] = set()
        for name, command in group.commands.items():
            qualified = f"{prefix}.{name}" if prefix else name
            if isinstance(command, Group):
                result.update(command_names(command, qualified))
            else:
                result.add(qualified)
        return result

    actual = command_names(root)
    assert actual == {command.name for command in COMMANDS}


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_shell_completion_is_generated_from_the_command_tree(
    shell: str, monkeypatch
) -> None:
    monkeypatch.setenv("_TYPER_COMPLETE_TEST_DISABLE_SHELL_DETECTION", "1")
    result = CliRunner().invoke(app, ["--show-completion", shell], prog_name="neomua")
    assert result.exit_code == 0, result.output
    assert "neomua" in result.stdout.lower()


def test_help_exposes_management_groups() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in (
        "agents",
        "skills",
        "mcp",
        "plugins",
        "releases",
        "approvals",
        "node",
    ):
        assert group in result.stdout


def test_agent_release_dry_run_uses_formal_validation(monkeypatch) -> None:
    calls = []

    def fake_call(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"status": "validated", "revision": 4, "errors": []}

    monkeypatch.setattr(cli_module, "_call", fake_call)
    result = CliRunner().invoke(
        app,
        [
            "agents",
            "release",
            "agent-1",
            "--revision",
            "4",
            "--version",
            "1.0.0",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert calls == [
        (
            "POST",
            "/agents/agent-1/draft/validate",
            {"as_json": True},
        )
    ]


def test_activation_partial_result_has_distinct_exit_code(monkeypatch) -> None:
    class FakeClient:
        def request(self, method, path, **kwargs):
            return {
                "id": "activation-1",
                "status": "partial",
                "deployments": [
                    {"runtime_profile_id": "runtime-1", "status": "applied"},
                    {"runtime_profile_id": "runtime-2", "status": "failed"},
                ],
            }

        def close(self):
            return None

    monkeypatch.setattr(cli_module, "_client", lambda: FakeClient())
    result = CliRunner().invoke(
        app,
        [
            "releases",
            "activate",
            "release-1",
            "--runtime",
            "runtime-1",
            "--runtime",
            "runtime-2",
            "--yes",
            "--json",
        ],
    )
    assert result.exit_code == 8
    assert json.loads(result.stdout)["status"] == "partial"
