import asyncio
import hashlib
import json
import os
import platform
import secrets
import shutil
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import typer
from runtime_worker.capabilities import (
    discover_runtime_capabilities,
    discover_runtime_installations,
    node_agent_version,
)
from runtime_worker.runtime_configuration import (
    RuntimeConfigurationStore,
    sanitize_process_environment,
)

from node_runtime.adapter_registry import (
    LocalInstallationRegistry,
    discover_client_installations,
)
from node_runtime.agent_releases import AgentReleaseController
from node_runtime.artifacts.controller import ArtifactController
from node_runtime.artifacts.installer import ArtifactInstaller
from node_runtime.config import ConfigStore, NodeConfig
from node_runtime.connection import NodeConnection
from node_runtime.distribution import (
    DistributionValidationError,
    download_distribution,
    verify_signed_manifest,
)
from node_runtime.identity import (
    DeviceIdentity,
    IdentityStore,
    generate_keypair,
    sign_device_payload,
)
from node_runtime.mcp_validation import NodeMcpValidationController
from node_runtime.model_route import ModelRouteStore
from node_runtime.reconcile import ReconcileState
from node_runtime.runtime_config import RuntimeConfigManager
from node_runtime.secrets import node_secret_fingerprints
from node_runtime.service import SystemdServiceManager, UnsupportedServiceManager
from node_runtime.skill_sync import NodeSkillSyncController
from node_runtime.spool import EventSpool
from node_runtime.tasks import NodeTaskController

app = typer.Typer(no_args_is_help=True)


def _require_https(platform_url: str) -> str:
    parsed = urlparse(platform_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise typer.BadParameter("platform-url must be an HTTPS URL")
    return platform_url.rstrip("/")


@app.command()
def install(
    platform_url: str = typer.Option(...),
    enrollment_token: str | None = typer.Option(default=None),
    mode: str = typer.Option(..., help="service or client"),
    signing_public_key: str = typer.Option(...),
    name: str = typer.Option(default_factory=socket.gethostname),
    state_dir: Path = typer.Option(Path("/var/lib/neomua-node")),
) -> None:
    """Enroll this device and install its managed system service."""
    if mode not in {"service", "client"}:
        raise typer.BadParameter("mode must be service or client")
    url = _require_https(platform_url)
    try:
        manager = SystemdServiceManager.detect()
    except UnsupportedServiceManager as exc:
        raise typer.BadParameter(str(exc)) from exc
    config_path = state_dir / "config.json"
    if state_dir.exists():
        raise RuntimeError(
            "existing node state requires explicit 'neomua-node repair'; install will not overwrite it"
        )
    if enrollment_token is None:
        enrollment_token = typer.prompt("Enrollment token", hide_input=True)
    keypair = generate_keypair()
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = state_dir.parent / f".{state_dir.name}.{secrets.token_hex(8)}.staging"
    staging.mkdir(mode=0o700, exist_ok=False)
    try:
        with httpx.Client(timeout=30) as client:
            time_response = client.get(f"{url}/api/v1/node/bootstrap/time")
            time_response.raise_for_status()
            server_time = datetime.fromisoformat(time_response.json()["server_time"])
            clock_skew_seconds = int(
                (datetime.now(timezone.utc) - server_time).total_seconds()
            )
            preflight = client.post(
                f"{url}/api/v1/node/bootstrap/preflight",
                json={
                    "token": enrollment_token,
                    "management_mode": mode,
                    "os_name": platform.system().lower(),
                    "architecture": platform.machine().lower(),
                    "agent_version": node_agent_version(),
                    "public_key": keypair.public_key,
                    "service_manager": "systemd",
                    "available_disk_bytes": shutil.disk_usage(state_dir.parent).free,
                    "state_directory_atomic_rename": True,
                    "platform_tls_verified": True,
                    "clock_skew_seconds": clock_skew_seconds,
                },
            )
            preflight.raise_for_status()
            signed_manifest = preflight.json()
            manifest = verify_signed_manifest(
                signed_manifest,
                trusted_signing_public_key=signing_public_key,
                mode=mode,
            )
            if manifest.get("node_manager_version") != node_agent_version():
                raise DistributionValidationError(
                    "bootstrap manifest Node Manager version mismatch"
                )
            release_id = str(manifest["release_id"])
            release_root = staging / "releases" / release_id
            download_distribution(
                client,
                platform_url=url,
                bootstrap_token=enrollment_token,
                manifest=manifest,
                destination=release_root,
            )
            idempotency_key = secrets.token_urlsafe(32)
            manifest_digest = str(signed_manifest["manifest_digest"])
            proof = sign_device_payload(
                keypair.private_key,
                (
                    f"neomua-enroll-v2:{enrollment_token}:{mode}:"
                    f"{manifest_digest}:{idempotency_key}"
                ),
            )
            components = manifest["components"]
            component_versions = {item["name"]: item["version"] for item in components}
            enrollment_payload = {
                "token": enrollment_token,
                "name": name,
                "hostname": socket.gethostname(),
                "os_name": platform.system().lower(),
                "architecture": platform.machine().lower(),
                "agent_version": node_agent_version(),
                "sdk_version": component_versions.get("claude-agent-sdk"),
                "harness_capabilities": {},
                "public_key": keypair.public_key,
                "management_mode": mode,
                "proof": proof,
                "idempotency_key": idempotency_key,
                "distribution_manifest_digest": manifest_digest,
            }
            try:
                response = client.post(
                    f"{url}/api/v1/node/enroll", json=enrollment_payload
                )
            except httpx.TransportError:
                recovery_proof = sign_device_payload(
                    keypair.private_key,
                    f"neomua-recover-v1:{enrollment_token}:{mode}",
                )
                response = client.post(
                    f"{url}/api/v1/node/bootstrap/recover",
                    json={**enrollment_payload, "proof": recovery_proof},
                )
            response.raise_for_status()
            enrolled = response.json()
        IdentityStore(staging / "identity.json").save(
            DeviceIdentity(
                node_id=enrolled["node_id"],
                namespace_id=enrolled["namespace_id"],
                private_key=keypair.private_key,
                credential=enrolled["credential"],
            )
        )
        ConfigStore(staging / "config.json").save(
            NodeConfig.model_validate(
                {
                    "platform_url": url,
                    "node_name": name,
                    "management_mode": mode,
                    "distribution_release_id": release_id,
                    "bootstrap_session_id": signed_manifest["bootstrap_session_id"],
                    "distribution_manifest_digest": manifest_digest,
                    "logical_installation_ref": manifest["logical_installation_ref"],
                    "distribution_entrypoint": str(
                        Path("releases") / release_id / manifest["entrypoint"]
                    ),
                    "distribution_components": components,
                    "adapter_registry": manifest.get("adapter_registry"),
                    "adapter_registry_digest": manifest.get("adapter_registry_digest"),
                    "distribution_signing_public_key": signing_public_key,
                    "artifact_roots": {
                        target: str(state_dir / "content" / target)
                        for target in (
                            "agents",
                            "skills",
                            "mcp",
                            "cli",
                            "workspace",
                        )
                    },
                }
            )
        )
        os.replace(staging, state_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    installed_entrypoint = state_dir / Path(
        ConfigStore(config_path).load().distribution_entrypoint or ""
    )
    first_applied_at = datetime.now(timezone.utc)
    receipt_payload = {
        "node_id": enrolled["node_id"],
        "bootstrap_session_id": signed_manifest.get("bootstrap_session_id"),
        "distribution_release_id": release_id,
        "manifest_digest": manifest_digest,
        "components": components,
        "logical_installation_ref": manifest["logical_installation_ref"],
        "first_applied_at": first_applied_at.isoformat(),
    }
    # The bootstrap session ID is returned at creation time, but the installer only
    # receives the secret. The preflight response binds it without exposing credentials.
    receipt_payload["bootstrap_session_id"] = signed_manifest["bootstrap_session_id"]
    receipt_digest = hashlib.sha256(
        json.dumps(
            receipt_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    receipt = httpx.post(
        f"{url}/api/v1/node/bootstrap/receipt",
        timeout=30,
        json={
            **receipt_payload,
            "device_signature": sign_device_payload(
                keypair.private_key,
                f"neomua-installation-receipt-v1:{receipt_digest}",
            ),
        },
    )
    receipt.raise_for_status()
    receipt_body = receipt.json()
    installed_config = ConfigStore(config_path).load()
    installed_config.installation_receipt_id = receipt_body["id"]
    ConfigStore(config_path).save(installed_config)
    stage_payload = {
        "node_id": enrolled["node_id"],
        "bootstrap_session_id": signed_manifest["bootstrap_session_id"],
        "stage": "service_activated",
        "error": None,
    }
    try:
        manager.install(str(installed_entrypoint), state_dir)
    except Exception:
        failed_payload = {
            **stage_payload,
            "stage": "failed",
            "error": {"code": "systemd_activation_failed"},
        }
        failed_digest = hashlib.sha256(
            json.dumps(
                failed_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        httpx.post(
            f"{url}/api/v1/node/bootstrap/stage",
            timeout=30,
            json={
                **failed_payload,
                "device_signature": sign_device_payload(
                    keypair.private_key,
                    f"neomua-bootstrap-stage-v1:{failed_digest}",
                ),
            },
        ).raise_for_status()
        raise
    stage_digest = hashlib.sha256(
        json.dumps(
            stage_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    stage_response = httpx.post(
        f"{url}/api/v1/node/bootstrap/stage",
        timeout=30,
        json={
            **stage_payload,
            "device_signature": sign_device_payload(
                keypair.private_key,
                f"neomua-bootstrap-stage-v1:{stage_digest}",
            ),
        },
    )
    stage_response.raise_for_status()
    typer.echo(f"Node {enrolled['node_id']} enrolled and service started")


@app.command()
def repair(
    state_dir: Path = typer.Option(Path("/var/lib/neomua-node")),
    resume_service: bool = typer.Option(
        False,
        help="Explicitly reinstall and start the systemd unit for the existing identity",
    ),
) -> None:
    """Diagnose an existing installation and optionally resume its service activation."""
    identity_path = state_dir / "identity.json"
    config_path = state_dir / "config.json"
    if not identity_path.is_file() or not config_path.is_file():
        raise RuntimeError("node identity or configuration is missing")
    identity = IdentityStore(identity_path).load()
    config = ConfigStore(config_path).load()
    if (
        config.distribution_entrypoint is None
        or config.bootstrap_session_id is None
        or config.installation_receipt_id is None
    ):
        raise RuntimeError(
            "installation state predates a recoverable bootstrap attempt"
        )
    resolved_state_dir = state_dir.resolve()
    entrypoint = (resolved_state_dir / config.distribution_entrypoint).resolve()
    if resolved_state_dir not in entrypoint.parents:
        raise RuntimeError("distribution entrypoint escapes the node state directory")
    manager = SystemdServiceManager.detect()
    diagnostics = {
        "node_id": identity.node_id,
        "management_mode": config.management_mode,
        "identity_mode": oct(identity_path.stat().st_mode & 0o777),
        "config_mode": oct(config_path.stat().st_mode & 0o777),
        "entrypoint_exists": entrypoint.is_file(),
        "entrypoint_executable": entrypoint.is_file()
        and os.access(entrypoint, os.X_OK),
        "distribution_manifest_digest": config.distribution_manifest_digest,
        "installation_receipt_id": config.installation_receipt_id,
        "systemd": manager.inspect(),
    }
    typer.echo(json.dumps(diagnostics, ensure_ascii=False, sort_keys=True))
    if not resume_service:
        return
    if identity_path.stat().st_mode & 0o077 or config_path.stat().st_mode & 0o077:
        raise RuntimeError("node state permissions are unsafe; no mutation performed")
    if not diagnostics["entrypoint_executable"]:
        raise RuntimeError(
            "distribution entrypoint is unavailable; no mutation performed"
        )
    manager.install(str(entrypoint), state_dir)
    stage_payload = {
        "node_id": identity.node_id,
        "bootstrap_session_id": config.bootstrap_session_id,
        "stage": "service_activated",
        "error": None,
    }
    stage_digest = hashlib.sha256(
        json.dumps(
            stage_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    response = httpx.post(
        f"{str(config.platform_url).rstrip('/')}/api/v1/node/bootstrap/stage",
        timeout=30,
        json={
            **stage_payload,
            "device_signature": sign_device_payload(
                identity.private_key,
                f"neomua-bootstrap-stage-v1:{stage_digest}",
            ),
        },
    )
    response.raise_for_status()
    typer.echo("Existing bootstrap attempt resumed; no re-enrollment was performed")


@app.command()
def run(state_dir: Path = typer.Option(Path("/var/lib/neomua-node"))) -> None:
    """Run the managed daemon in the foreground."""
    config = ConfigStore(state_dir / "config.json").load()
    identity_store = IdentityStore(state_dir / "identity.json")
    identity = identity_store.load()
    source_environment = sanitize_process_environment()
    spool = EventSpool(state_dir / "events.db")
    interrupted_task_ids = spool.recover_interrupted_dispatches()
    last_ack, spool_first, spool_last = spool.reconciliation_range()
    route_store = ModelRouteStore(state_dir / "model-routes.json")
    runtime_configuration_store = RuntimeConfigurationStore(
        state_dir / "runtime-configurations.json",
        source_environment=source_environment,
    )
    agent_release_controller = AgentReleaseController(
        identity.node_id, state_dir / "agent-releases"
    )
    skill_sync_controller = NodeSkillSyncController(
        str(config.platform_url), identity.node_id, state_dir / "skill-cache"
    )
    task_controller = NodeTaskController(
        identity.node_id,
        spool,
        route_store,
        runtime_configuration_store=runtime_configuration_store,
        release_store=agent_release_controller.store,
        skill_store=skill_sync_controller.store,
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
    discovery_observations: list[dict[str, object]] = []
    discovery_provider = None
    if config.management_mode == "client":
        if (
            config.adapter_registry is None
            or config.adapter_registry_digest is None
            or config.distribution_signing_public_key is None
        ):
            raise RuntimeError("signed client Adapter Registry is incomplete")
        local_installation_registry = LocalInstallationRegistry(
            state_dir / "client-installations.json"
        )

        def discover_client() -> tuple[
            list[dict[str, object]], list[dict[str, object]]
        ]:
            return discover_client_installations(
                config.adapter_registry or {},
                registry_digest=config.adapter_registry_digest or "",
                trusted_signing_public_key=(
                    config.distribution_signing_public_key or ""
                ),
                local_registry=local_installation_registry,
            )

        discovery_provider = discover_client
        runtime_installations, discovery_observations = discover_client()
    else:
        runtime_installations = discover_runtime_installations(
            mode=config.management_mode
        )
    if config.management_mode == "service":
        if (
            config.installation_receipt_id is None
            or config.distribution_manifest_digest is None
            or config.logical_installation_ref is None
        ):
            raise RuntimeError("managed service installation receipt is incomplete")
        for installation in runtime_installations:
            installation["installation_key"] = config.logical_installation_ref
            installation["installation_receipt_id"] = config.installation_receipt_id
            installation["distribution_manifest_digest"] = (
                config.distribution_manifest_digest
            )
            capabilities = installation.get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["distribution_manifest_digest"] = (
                    config.distribution_manifest_digest
                )
    connection = NodeConnection(
        str(config.platform_url),
        identity,
        ReconcileState(
            config_revision=route_store.revision(),
            discovery_generation=route_store.discovery_generation(),
            interrupted_task_ids=interrupted_task_ids,
            last_acknowledged_event=last_ack,
            spool_first_sequence=spool_first,
            spool_last_sequence=spool_last,
        ),
        identity_store=identity_store,
        task_controller=task_controller,
        runtime_configuration_store=runtime_configuration_store,
        runtime_config_manager=RuntimeConfigManager(route_store),
        artifact_controller=artifact_controller,
        harness_capabilities=discover_runtime_capabilities(mode=config.management_mode),
        secret_fingerprints=lambda: node_secret_fingerprints(secret_index),
        agent_release_controller=agent_release_controller,
        mcp_validation_controller=NodeMcpValidationController(identity.node_id),
        skill_sync_controller=skill_sync_controller,
        runtime_installations=runtime_installations,
        discovery_observations=discovery_observations,
        adapter_registry_digest=config.adapter_registry_digest,
        discovery_provider=discovery_provider,
        on_discovery_generation=route_store.set_discovery_generation,
    )
    asyncio.run(connection.run_forever())
