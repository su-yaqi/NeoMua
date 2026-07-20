import hashlib
from pathlib import Path

import pytest

from runtime_worker.agent_shell import ClaudeCodeShell, RunCommand
from runtime_worker.runtime_configuration import (
    AppliedRuntimeConfiguration,
    RuntimeConfigurationStore,
)


def _client_executable(path: Path, login_state: Path) -> str:
    path.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "Claude Code 1.2.3"; exit 0; fi\n'
        f'if [ "$1" = "auth" ]; then /bin/cat "{login_state}"; exit 0; fi\n',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _configuration(executable: Path, fingerprint: str) -> AppliedRuntimeConfiguration:
    return AppliedRuntimeConfiguration(
        runtime_instance_id="00000000-0000-0000-0000-000000000001",
        engine_type="claude_code",
        engine_version="1.2.3",
        adapter_version="0.1.0",
        configuration_revision_id="00000000-0000-0000-0000-000000000002",
        configuration_digest="a" * 64,
        executable="claude",
        working_directory_policy="workspace",
        capability_fingerprint="b" * 64,
        adapter_execution_ref=(
            "client:neomua.claude-code:00000000-0000-0000-0000-000000000003"
        ),
        local_execution_path=str(executable),
        local_executable_fingerprint=fingerprint,
        local_revalidation_contract={
            "version_args": ["--version"],
            "version_regex": r"Claude Code ([0-9.]+)",
            "identity_regex": r"Claude Code",
            "login_probe_args": ["auth"],
            "login_success_regex": r"logged-in",
            "timeout_seconds": 2,
            "max_output_bytes": 4096,
        },
    )


def test_client_execution_revalidates_login_and_omits_stale_heartbeat(
    tmp_path: Path,
) -> None:
    login_state = tmp_path / "login-state"
    login_state.write_text("logged-in\n", encoding="utf-8")
    executable = tmp_path / "claude"
    configuration = _configuration(
        executable, _client_executable(executable, login_state)
    )
    store = RuntimeConfigurationStore(tmp_path / "runtime-configurations.json")
    store.save(configuration)

    assert configuration.execution_path() == str(executable)
    assert len(store.capability_evidence()) == 1
    login_state.write_text("logged-out\n", encoding="utf-8")
    with pytest.raises(ValueError, match="runtime_native_login_invalid"):
        configuration.execution_path()
    assert store.capability_evidence() == []


@pytest.mark.anyio
async def test_client_shell_has_no_path_lookup_fallback() -> None:
    shell = ClaudeCodeShell()
    events = shell.run_session(
        RunCommand(prompt="hello", model="model-a", engine_type="claude_code")
    )
    with pytest.raises(ValueError, match="executable is not installed"):
        await anext(events)
