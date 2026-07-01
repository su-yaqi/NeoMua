import asyncio
import platform
import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
import typer

from node_runtime.config import ConfigStore, NodeConfig
from node_runtime.identity import DeviceIdentity, IdentityStore, generate_keypair
from node_runtime.connection import NodeConnection
from node_runtime.reconcile import ReconcileState
from node_runtime.service import SystemdServiceManager, UnsupportedServiceManager

app = typer.Typer(no_args_is_help=True)


def _require_https(platform_url: str) -> str:
    parsed = urlparse(platform_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise typer.BadParameter("platform-url must be an HTTPS URL")
    return platform_url.rstrip("/")


@app.command()
def install(
    platform_url: str = typer.Option(...),
    enrollment_token: str = typer.Option(..., prompt=True, hide_input=True),
    name: str = typer.Option(default_factory=socket.gethostname),
    state_dir: Path = typer.Option(Path("/var/lib/neomua-node")),
) -> None:
    """Enroll this device and install its managed system service."""
    url = _require_https(platform_url)
    try:
        manager = SystemdServiceManager.detect()
    except UnsupportedServiceManager as exc:
        raise typer.BadParameter(str(exc)) from exc
    keypair = generate_keypair()
    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{url}/api/v1/node/enroll",
            json={
                "token": enrollment_token,
                "name": name,
                "hostname": socket.gethostname(),
                "os_name": platform.system().lower(),
                "architecture": platform.machine().lower(),
                "agent_version": "0.1.0",
                "sdk_version": "0.2.110",
                "public_key": keypair.public_key,
            },
        )
        response.raise_for_status()
        enrolled = response.json()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    IdentityStore(state_dir / "identity.json").save(
        DeviceIdentity(
            node_id=enrolled["node_id"],
            private_key=keypair.private_key,
            credential=enrolled["credential"],
        )
    )
    ConfigStore(state_dir / "config.json").save(
        NodeConfig(platform_url=url, node_name=name)
    )
    executable = shutil.which("neomua-node")
    if not executable:
        raise RuntimeError("neomua-node executable is not available on PATH")
    manager.install(executable, state_dir)
    typer.echo(f"Node {enrolled['node_id']} enrolled and service started")


@app.command()
def run(state_dir: Path = typer.Option(Path("/var/lib/neomua-node"))) -> None:
    """Run the managed daemon in the foreground."""
    config = ConfigStore(state_dir / "config.json").load()
    identity_store = IdentityStore(state_dir / "identity.json")
    identity = identity_store.load()
    connection = NodeConnection(
        str(config.platform_url), identity, ReconcileState(), identity_store=identity_store
    )
    asyncio.run(connection.run_forever())
