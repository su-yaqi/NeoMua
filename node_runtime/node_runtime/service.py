import platform
import subprocess
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
        unit = (
            "[Unit]\nDescription=NeoMua Node Runtime\nAfter=network-online.target\n"
            "Wants=network-online.target\n\n[Service]\nType=simple\n"
            f"ExecStart={executable} run --state-dir {state_dir}\n"
            "Restart=always\nRestartSec=5\nNoNewPrivileges=true\n"
            "ProtectSystem=strict\nProtectHome=true\n"
            f"ReadWritePaths={state_dir}\n\n[Install]\nWantedBy=multi-user.target\n"
        )
        self.unit_path.write_text(unit, encoding="utf-8")
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(
            ["systemctl", "enable", "--now", "neomua-node.service"], check=True
        )
