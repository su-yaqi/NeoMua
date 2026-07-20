from pathlib import Path

import pytest

from node_runtime.service import SystemdServiceManager


def test_systemd_unit_paths_reject_injection_characters() -> None:
    assert (
        SystemdServiceManager._safe_unit_path(
            "/usr/local/bin/neomua-node", "executable"
        )
        == "/usr/local/bin/neomua-node"
    )
    with pytest.raises(ValueError, match="without whitespace"):
        SystemdServiceManager._safe_unit_path(
            "/var/lib/neomua\nExecStart=/bin/sh", "state_dir"
        )
    with pytest.raises(ValueError, match="absolute path"):
        SystemdServiceManager._safe_unit_path("relative/node", "executable")


def test_systemd_state_directory_is_an_absolute_path() -> None:
    state_dir = Path("/var/lib/neomua-node")
    assert SystemdServiceManager._safe_unit_path(str(state_dir), "state_dir") == str(
        state_dir
    )
