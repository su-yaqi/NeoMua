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
from node_runtime.model_route import ModelRouteStore
from node_runtime.spool import EventSpool
from node_runtime.tasks import NodeTaskController
from node_runtime.runtime_config import RuntimeConfigManager
from node_runtime.artifacts.controller import ArtifactController
from node_runtime.artifacts.installer import ArtifactInstaller
from node_runtime.agent_releases import AgentReleaseController
from node_runtime.mcp_validation import NodeMcpValidationController
from node_runtime.secrets import node_secret_fingerprints
from node_runtime.service import SystemdServiceManager, UnsupportedServiceManager
from runtime_worker.capabilities import (
    discover_harness_capabilities,
    node_agent_version,
)

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
    harness_capabilities = discover_harness_capabilities()
    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{url}/api/v1/node/enroll",
            json={
                "token": enrollment_token,
                "name": name,
                "hostname": socket.gethostname(),
                "os_name": platform.system().lower(),
                "architecture": platform.machine().lower(),
                "agent_version": node_agent_version(),
                "sdk_version": harness_capabilities["claude_code"]["sdk_version"],
                "harness_capabilities": harness_capabilities,
                "public_key": keypair.public_key,
            },
        )
        response.raise_for_status()
        enrolled = response.json()
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    IdentityStore(state_dir / "identity.json").save(
        DeviceIdentity(
            node_id=enrolled["node_id"],
            namespace_id=enrolled["namespace_id"],
            private_key=keypair.private_key,
            credential=enrolled["credential"],
        )
    )
    ConfigStore(state_dir / "config.json").save(
        NodeConfig(
            platform_url=url,
            node_name=name,
            artifact_roots={
                target: str(state_dir / "content" / target)
                for target in ("agents", "skills", "mcp", "cli", "workspace")
            },
        )
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
    spool = EventSpool(state_dir / "events.db")
    interrupted_task_ids = spool.recover_interrupted_dispatches()
    last_ack, spool_first, spool_last = spool.reconciliation_range()
    route_store = ModelRouteStore(state_dir / "model-routes.json")
    agent_release_controller = AgentReleaseController(
        identity.node_id, state_dir / "agent-releases"
    )
    task_controller = NodeTaskController(
        identity.node_id,
        spool,
        route_store,
        release_store=agent_release_controller.store,
    )
    installer = ArtifactInstaller(
        {key: Path(value) for key, value in config.artifact_roots.items()},
        identity.node_id,
        identity.namespace_id or "",
    )
    installer.recover()
    artifact_controller = ArtifactController(
        str(config.platform_url), identity.node_id, installer, state_dir / "downloads"
    )
    secret_index = state_dir / "node-secret-refs.json"
    node_secret_fingerprints(secret_index)
    connection = NodeConnection(
        str(config.platform_url),
        identity,
        ReconcileState(
            config_revision=route_store.revision(),
            interrupted_task_ids=interrupted_task_ids,
            last_acknowledged_event=last_ack,
            spool_first_sequence=spool_first,
            spool_last_sequence=spool_last,
        ),
        identity_store=identity_store,
        task_controller=task_controller,
        runtime_config_manager=RuntimeConfigManager(route_store),
        artifact_controller=artifact_controller,
        harness_capabilities=discover_harness_capabilities(),
        secret_fingerprints=lambda: node_secret_fingerprints(secret_index),
        agent_release_controller=agent_release_controller,
        mcp_validation_controller=NodeMcpValidationController(identity.node_id),
    )
    asyncio.run(connection.run_forever())
