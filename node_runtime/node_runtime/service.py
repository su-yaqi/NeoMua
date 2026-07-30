import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path


class UnsupportedServiceManager(RuntimeError):
    pass


class SystemdServiceManager:
    unit_path = Path("/etc/systemd/system/neomua-node.service")

    @classmethod
    def detect(cls) -> "SystemdServiceManager":
        if platform.system() != "Linux" or not Path("/run/systemd/system").exists():
            raise UnsupportedServiceManager(
                "Only Linux systems running systemd are currently supported"
            )
        return cls()

    def install(self, executable: str, state_dir: Path) -> None:
        safe_executable = self._safe_unit_path(executable, "executable")
        safe_state_dir = self._safe_unit_path(str(state_dir), "state_dir")
        unit = (
            "[Unit]\nDescription=NeoMua Node Runtime\nAfter=network-online.target\n"
            "Wants=network-online.target\n\n[Service]\nType=simple\n"
            f"ExecStart={safe_executable} run --state-dir {safe_state_dir}\n"
            "Restart=always\nRestartSec=5\nNoNewPrivileges=true\n"
            "ProtectSystem=strict\nProtectHome=true\n"
            f"ReadWritePaths={safe_state_dir}\n\n[Install]\nWantedBy=multi-user.target\n"
        )
        previous = self.unit_path.read_bytes() if self.unit_path.exists() else None
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".neomua-node.", suffix=".service", dir=self.unit_path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(unit)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.unit_path)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(
                ["systemctl", "enable", "--now", "neomua-node.service"],
                check=True,
            )
        except Exception:
            if previous is None:
                self.unit_path.unlink(missing_ok=True)
            else:
                self.unit_path.write_bytes(previous)
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def inspect(self) -> dict[str, object]:
        unit_exists = self.unit_path.is_file()
        enabled = subprocess.run(
            ["systemctl", "is-enabled", "neomua-node.service"],
            capture_output=True,
            check=False,
            text=True,
        )
        active = subprocess.run(
            ["systemctl", "is-active", "neomua-node.service"],
            capture_output=True,
            check=False,
            text=True,
        )
        return {
            "unit_exists": unit_exists,
            "enabled": enabled.returncode == 0,
            "active": active.returncode == 0,
            "enabled_state": enabled.stdout.strip(),
            "active_state": active.stdout.strip(),
        }

    @staticmethod
    def _safe_unit_path(value: str, field: str) -> str:
        if (
            not Path(value).is_absolute()
            or re.fullmatch(r"[A-Za-z0-9_./:@+\-]+", value) is None
        ):
            raise ValueError(
                f"{field} must be an absolute path without whitespace or control characters"
            )
        return value.replace("%", "%%")
