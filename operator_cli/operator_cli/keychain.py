import platform
import shutil
import subprocess

SERVICE = "io.neomua.operator-cli"


class KeychainUnavailable(RuntimeError):
    pass


class SystemKeychain:
    def _backend(self) -> str:
        system = platform.system()
        if system == "Darwin" and shutil.which("security"):
            return "macos"
        if system == "Linux" and shutil.which("secret-tool"):
            return "secret-tool"
        raise KeychainUnavailable(
            "No supported OS Keychain/credential manager is available; plaintext fallback is forbidden"
        )

    def set(self, item_id: str, value: str) -> None:
        backend = self._backend()
        if backend == "macos":
            subprocess.run(
                [
                    "security",
                    "add-generic-password",
                    "-U",
                    "-s",
                    SERVICE,
                    "-a",
                    item_id,
                    "-w",
                    value,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        else:
            subprocess.run(
                [
                    "secret-tool",
                    "store",
                    "--label",
                    "NeoMua Operator CLI",
                    "service",
                    SERVICE,
                    "account",
                    item_id,
                ],
                input=value,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )

    def get(self, item_id: str) -> str:
        backend = self._backend()
        command = (
            ["security", "find-generic-password", "-s", SERVICE, "-a", item_id, "-w"]
            if backend == "macos"
            else ["secret-tool", "lookup", "service", SERVICE, "account", item_id]
        )
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError("Credential is missing from the system Keychain")
        return result.stdout.strip()

    def delete(self, item_id: str) -> None:
        backend = self._backend()
        command = (
            ["security", "delete-generic-password", "-s", SERVICE, "-a", item_id]
            if backend == "macos"
            else ["secret-tool", "clear", "service", SERVICE, "account", item_id]
        )
        subprocess.run(
            command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
