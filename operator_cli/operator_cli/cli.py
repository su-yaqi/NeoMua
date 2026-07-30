import getpass
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import httpx
import typer

from operator_cli.client import ApiClient, ApiError
from operator_cli.keychain import SystemKeychain
from operator_cli.profiles import Profile, ProfileStore

app = typer.Typer(no_args_is_help=True, help="NeoMua Operator CLI")
auth_app = typer.Typer(no_args_is_help=True)
profile_app = typer.Typer(no_args_is_help=True)
agents_app = typer.Typer(no_args_is_help=True)
skills_app = typer.Typer(no_args_is_help=True)
tools_app = typer.Typer(no_args_is_help=True)
mcp_app = typer.Typer(no_args_is_help=True)
plugins_app = typer.Typer(no_args_is_help=True)
releases_app = typer.Typer(no_args_is_help=True)
runtimes_app = typer.Typer(no_args_is_help=True)
tasks_app = typer.Typer(no_args_is_help=True)
approvals_app = typer.Typer(no_args_is_help=True)
node_app = typer.Typer(no_args_is_help=True)
node_secret_app = typer.Typer(no_args_is_help=True)

for name, group in (
    ("auth", auth_app),
    ("profile", profile_app),
    ("agents", agents_app),
    ("skills", skills_app),
    ("tools", tools_app),
    ("mcp", mcp_app),
    ("plugins", plugins_app),
    ("releases", releases_app),
    ("runtimes", runtimes_app),
    ("tasks", tasks_app),
    ("approvals", approvals_app),
    ("node", node_app),
):
    app.add_typer(group, name=name)
node_app.add_typer(node_secret_app, name="secret")


def _emit(value: Any, *, as_json: bool = False) -> None:
    if as_json:
        typer.echo(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    elif isinstance(value, dict) and isinstance(value.get("data"), list):
        for item in value["data"]:
            typer.echo(
                f"{item.get('id', '')}\t{item.get('name') or item.get('slug') or item.get('status', '')}"
            )
    else:
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _client(profile_name: str | None = None) -> ApiClient:
    store = ProfileStore()
    profile = store.get(profile_name)
    if not profile.namespace_id:
        raise RuntimeError(
            "The active profile has no namespace; run profile set-namespace"
        )
    client = ApiClient(profile, store, SystemKeychain())
    client.verify_protocol()
    return client


def _call(
    method: str,
    path: str,
    *,
    body: Any = None,
    as_json: bool = False,
    idempotent: bool = False,
) -> Any:
    client = _client()
    try:
        value = client.request(
            method, path, json_body=body, idempotent_mutation=idempotent
        )
        _emit(value, as_json=as_json)
        return value
    finally:
        client.close()


def _validation_exit(value: Any) -> None:
    if isinstance(value, dict) and (
        value.get("status") == "error" or value.get("compatible") is False
    ):
        raise typer.Exit(6)


def _partial_exit(value: Any) -> None:
    if isinstance(value, dict) and value.get("status") == "partial":
        raise typer.Exit(8)


def _confirm(action: str, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise RuntimeError(f"{action} requires --yes in non-interactive mode")
    typer.confirm(action, abort=True)


@auth_app.command("login")
def login(
    api_url: str = typer.Option(...),
    email: str = typer.Option(...),
    profile: str = typer.Option("default"),
    namespace: str | None = typer.Option(None),
) -> None:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Login requires an interactive TTY for hidden password input"
        )
    password = getpass.getpass("Password: ")
    store = ProfileStore()
    normalized = store.validate_url(api_url)
    credential_id = f"profile:{profile}:{uuid.uuid4()}"
    with httpx.Client(
        base_url=normalized, timeout=30, follow_redirects=False
    ) as client:
        response = client.post(
            "/api/v1/cli/login",
            json={
                "email": email,
                "password": password,
                "client_name": "neomua-cli/0.1.0",
            },
        )
    password = ""
    if response.status_code != 200:
        raise ApiError(response)
    payload = response.json()
    keychain = SystemKeychain()
    keychain.set(credential_id, payload["refresh_token"])
    try:
        store.put(
            Profile(
                name=profile,
                api_url=normalized,
                namespace_id=namespace,
                output="human",
                credential_id=credential_id,
            )
        )
    except Exception:
        keychain.delete(credential_id)
        raise
    typer.echo(f"Logged in with profile {profile}")


@auth_app.command("status")
def auth_status(as_json: bool = typer.Option(False, "--json")) -> None:
    client = _client()
    try:
        _emit(client.request("GET", "/users/me"), as_json=as_json)
    finally:
        client.close()


@auth_app.command("logout")
def logout(yes: bool = typer.Option(False, "--yes")) -> None:
    _confirm("Log out and revoke this CLI session?", yes)
    store = ProfileStore()
    profile = store.get()
    keychain = SystemKeychain()
    refresh = keychain.get(profile.credential_id)
    client = ApiClient(profile, store, keychain)
    try:
        client.request("POST", "/cli/logout", json_body={"refresh_token": refresh})
    finally:
        client.close()
        keychain.delete(profile.credential_id)
    typer.echo("Logged out")


@profile_app.command("list")
def profile_list(as_json: bool = typer.Option(False, "--json")) -> None:
    value = ProfileStore().load()
    safe = {
        "current": value.get("current"),
        "profiles": list(value["profiles"].values()),
    }
    _emit(safe, as_json=as_json)


@profile_app.command("use")
def profile_use(name: str) -> None:
    store = ProfileStore()
    value = store.load()
    if name not in value["profiles"]:
        raise RuntimeError(f"Profile not found: {name}")
    value["current"] = name
    store.save(value)


@profile_app.command("set-namespace")
def profile_set_namespace(namespace_id: str) -> None:
    uuid.UUID(namespace_id)
    store = ProfileStore()
    profile = store.get()
    store.put(Profile(**{**profile.__dict__, "namespace_id": namespace_id}))


@profile_app.command("delete")
def profile_delete(name: str, yes: bool = typer.Option(False, "--yes")) -> None:
    _confirm(f"Delete profile {name}?", yes)
    profile = ProfileStore().delete(name)
    SystemKeychain().delete(profile.credential_id)


@agents_app.command("list")
def agents_list(as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", "/agents", as_json=as_json)


@agents_app.command("get")
def agents_get(agent_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", f"/agents/{agent_id}", as_json=as_json)


@agents_app.command("validate")
def agents_validate(
    agent_id: str, as_json: bool = typer.Option(False, "--json")
) -> None:
    _call("POST", f"/agents/{agent_id}/draft/validate", as_json=as_json)


@agents_app.command("release")
def agents_release(
    agent_id: str,
    revision: int = typer.Option(...),
    version: str = typer.Option(...),
    yes: bool = typer.Option(False, "--yes"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    if dry_run:
        value = _call("POST", f"/agents/{agent_id}/draft/validate", as_json=as_json)
        if isinstance(value, dict) and int(value.get("revision", -1)) != revision:
            raise RuntimeError(
                f"Validated draft revision {value.get('revision')} does not match requested revision {revision}"
            )
        _validation_exit(value)
        return
    _confirm(f"Create immutable Agent Release {version}?", yes)
    _call(
        "POST",
        f"/agents/{agent_id}/releases",
        body={"draft_revision": revision, "version": version},
        as_json=as_json,
        idempotent=True,
    )


@skills_app.command("list")
def skills_list(as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", "/skills", as_json=as_json)


@skills_app.command("get")
def skills_get(skill_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", f"/skills/{skill_id}", as_json=as_json)


@skills_app.command("upload")
def skills_upload(
    skill_id: str,
    version: str = typer.Option(...),
    file: Path = typer.Option(..., exists=True, dir_okay=False),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    client = _client()
    try:
        with file.open("rb") as source:
            value = client.request(
                "POST",
                f"/skills/{skill_id}/versions",
                data_body={"version": version},
                files={"file": (file.name, source, "application/zip")},
                idempotent_mutation=True,
            )
        _emit(value, as_json=as_json)
    finally:
        client.close()


@skills_app.command("deprecate")
def skills_deprecate(
    skill_id: str, version: str, yes: bool = typer.Option(False, "--yes")
) -> None:
    _confirm(f"Deprecate Skill {version}?", yes)
    _call("POST", f"/skills/{skill_id}/versions/{version}/deprecate")


@tools_app.command("list")
def tools_list(as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", "/tools/catalog", as_json=as_json)


@mcp_app.command("list")
def mcp_list(as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", "/mcp-servers", as_json=as_json)


@mcp_app.command("get")
def mcp_get(server_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", f"/mcp-servers/{server_id}", as_json=as_json)


@mcp_app.command("validate")
def mcp_validate(target_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    _call(
        "POST", f"/mcp-targets/{target_id}/validate", as_json=as_json, idempotent=True
    )


@plugins_app.command("list")
def plugins_list(as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", "/plugins", as_json=as_json)


@plugins_app.command("get")
def plugins_get(plugin_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", f"/plugins/{plugin_id}", as_json=as_json)


@plugins_app.command("validate")
def plugins_validate(
    plugin_id: str, as_json: bool = typer.Option(False, "--json")
) -> None:
    _call("POST", f"/plugins/{plugin_id}/draft/validate", as_json=as_json)


@plugins_app.command("publish")
def plugins_publish(
    plugin_id: str,
    revision: int = typer.Option(...),
    version: str = typer.Option(...),
    yes: bool = typer.Option(False, "--yes"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    if dry_run:
        value = _call("POST", f"/plugins/{plugin_id}/draft/validate", as_json=as_json)
        if isinstance(value, dict) and int(value.get("revision", -1)) != revision:
            raise RuntimeError(
                f"Validated draft revision {value.get('revision')} does not match requested revision {revision}"
            )
        _validation_exit(value)
        return
    _confirm(f"Publish immutable Plugin {version}?", yes)
    _call(
        "POST",
        f"/plugins/{plugin_id}/versions",
        body={"draft_revision": revision, "version": version},
        as_json=as_json,
        idempotent=True,
    )


@releases_app.command("get")
def release_get(release_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", f"/agent-releases/{release_id}", as_json=as_json)


@releases_app.command("activate")
def release_activate(
    release_id: str,
    runtime: list[str] = typer.Option(...),
    yes: bool = typer.Option(False, "--yes"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    wait_seconds: int = typer.Option(0, min=0, max=86400),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    body = {"runtime_profile_ids": runtime}
    if dry_run:
        value = _call(
            "POST",
            f"/agent-releases/{release_id}/activations/precheck",
            body=body,
            as_json=as_json,
        )
        _validation_exit(value)
        return
    _confirm(f"Activate Release on {len(runtime)} explicit target(s)?", yes)
    client = _client()
    try:
        value = client.request(
            "POST",
            f"/agent-releases/{release_id}/activations",
            json_body=body,
            idempotent_mutation=True,
        )
        deadline = time.monotonic() + wait_seconds
        while (
            wait_seconds
            and isinstance(value, dict)
            and value.get("status") in {"validating", "deploying"}
            and time.monotonic() < deadline
        ):
            time.sleep(min(1, max(0, deadline - time.monotonic())))
            value = client.request("GET", f"/agent-activations/{value['id']}")
        _emit(value, as_json=as_json)
        _partial_exit(value)
    finally:
        client.close()


@releases_app.command("retry")
def release_retry(
    deployment_id: str,
    yes: bool = typer.Option(False, "--yes"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    _confirm("Retry this failed deployment after live precheck?", yes)
    _call("POST", f"/agent-deployments/{deployment_id}/retry", as_json=as_json)


@releases_app.command("rollback")
def release_rollback(
    deployment_id: str,
    yes: bool = typer.Option(False, "--yes"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    _confirm("Create an audited rollback activation?", yes)
    _call(
        "POST",
        f"/agent-deployments/{deployment_id}/rollback",
        as_json=as_json,
        idempotent=True,
    )


@runtimes_app.command("list")
def runtimes_list(as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", "/runtime-agents", as_json=as_json)


@runtimes_app.command("get")
def runtimes_get(
    binding_id: str, as_json: bool = typer.Option(False, "--json")
) -> None:
    _call("GET", f"/runtime-agents/{binding_id}", as_json=as_json)


@tasks_app.command("create")
def tasks_create(
    runtime_agent_release_id: str = typer.Option(...),
    runtime_profile_id: str = typer.Option(...),
    prompt: str = typer.Option(..., prompt=True),
    node_id: str | None = typer.Option(None),
    task_kind: str = typer.Option("ordinary"),
    working_directory: str | None = typer.Option(None),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    body = {
        "runtime_agent_release_id": runtime_agent_release_id,
        "runtime_profile_id": runtime_profile_id,
        "prompt": prompt,
        "node_id": node_id,
        "task_kind": task_kind,
        "working_directory": working_directory,
    }
    _call("POST", "/runtime-tasks", body=body, as_json=as_json, idempotent=True)


@tasks_app.command("get")
def tasks_get(task_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", f"/runtime-tasks/{task_id}", as_json=as_json)


@tasks_app.command("cancel")
def tasks_cancel(task_id: str, yes: bool = typer.Option(False, "--yes")) -> None:
    _confirm("Cancel this task?", yes)
    _call("POST", f"/runtime-tasks/{task_id}/cancel")


@tasks_app.command("events")
def tasks_events(task_id: str, as_json: bool = typer.Option(False, "--json")) -> None:
    _call("GET", f"/runtime-tasks/{task_id}/events", as_json=as_json)


@tasks_app.command("retry")
def tasks_retry(
    task_id: str,
    yes: bool = typer.Option(False, "--yes"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    _confirm("Retry this failed or interrupted task with its frozen snapshot?", yes)
    _call("POST", f"/runtime-tasks/{task_id}/retry", as_json=as_json, idempotent=True)


@tasks_app.command("approvals")
def tasks_approvals(
    task_id: str, as_json: bool = typer.Option(False, "--json")
) -> None:
    _call("GET", f"/runtime-tasks/{task_id}/approvals", as_json=as_json)


def _approval(
    approval_id: str,
    decision: str,
    args_digest: str,
    reason: str | None,
    yes: bool,
    as_json: bool,
) -> None:
    _confirm(f"{decision.title()} this exact Tool Call?", yes)
    _call(
        "POST",
        f"/tool-approvals/{approval_id}/{decision}",
        body={"args_digest": args_digest, "reason": reason},
        as_json=as_json,
    )


@approvals_app.command("approve")
def approval_approve(
    approval_id: str,
    args_digest: str = typer.Option(...),
    reason: str | None = typer.Option(None),
    yes: bool = typer.Option(False, "--yes"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    _approval(approval_id, "approve", args_digest, reason, yes, as_json)


@approvals_app.command("deny")
def approval_deny(
    approval_id: str,
    args_digest: str = typer.Option(...),
    reason: str | None = typer.Option(None),
    yes: bool = typer.Option(False, "--yes"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    _approval(approval_id, "deny", args_digest, reason, yes, as_json)


def _secret_index() -> Path:
    root = Path(os.environ.get("NEOMUA_NODE_STATE_DIR", "/var/lib/neomua-node"))
    identity_path = root / "identity.json"
    if not identity_path.is_file():
        raise RuntimeError(
            f"Node identity is unavailable at {identity_path}; run this command on an installed Node Runtime"
        )
    return root / "node-secret-refs.json"


def _load_secret_index() -> dict[str, dict[str, str]]:
    path = _secret_index()
    if not path.exists():
        return {}
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Node secret index is damaged")
    return cast(dict[str, dict[str, str]], value)


def _save_secret_index(value: dict[str, dict[str, str]]) -> None:
    path = _secret_index()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), "utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


@node_secret_app.command("set")
def node_secret_set(ref: str) -> None:
    if not sys.stdin.isatty():
        raise RuntimeError(
            "node secret set requires a hidden TTY prompt; --value is intentionally unsupported"
        )
    value = getpass.getpass(f"Secret for {ref}: ")
    if not value:
        raise RuntimeError("Secret may not be empty")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Node MCP secret must be entered as a JSON object of adapter fields"
        ) from exc
    if not isinstance(parsed, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in parsed.items()
    ):
        raise RuntimeError("Node MCP secret must contain string keys and values")
    item_id = f"node-secret:{ref}"
    SystemKeychain().set(item_id, value)
    fingerprint = hashlib.sha256(
        json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    value = ""
    index = _load_secret_index()
    index[ref] = {
        "credential_id": item_id,
        "fingerprint": fingerprint,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_secret_index(index)
    typer.echo(json.dumps({"ref": ref, "exists": True, "fingerprint": fingerprint}))


@node_secret_app.command("list")
def node_secret_list(as_json: bool = typer.Option(False, "--json")) -> None:
    values = [
        {
            "ref": ref,
            "exists": True,
            "fingerprint": item["fingerprint"],
            "updated_at": item.get("updated_at"),
        }
        for ref, item in sorted(_load_secret_index().items())
    ]
    _emit({"data": values, "count": len(values)}, as_json=as_json)


@node_secret_app.command("status")
def node_secret_status(ref: str, as_json: bool = typer.Option(False, "--json")) -> None:
    item = _load_secret_index().get(ref)
    _emit(
        {
            "ref": ref,
            "exists": item is not None,
            "fingerprint": item.get("fingerprint") if item else None,
            "updated_at": item.get("updated_at") if item else None,
        },
        as_json=as_json,
    )


@node_secret_app.command("remove")
def node_secret_remove(ref: str, yes: bool = typer.Option(False, "--yes")) -> None:
    _confirm(f"Remove node-local secret {ref}?", yes)
    index = _load_secret_index()
    item = index.pop(ref, None)
    if item:
        SystemKeychain().delete(item["credential_id"])
        _save_secret_index(index)


def main() -> None:
    try:
        app()
    except ApiError as exc:
        typer.echo(str(exc), err=True)
        code = {401: 3, 403: 4, 409: 5, 422: 6}.get(exc.status_code, 7)
        raise typer.Exit(code)
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(7)


if __name__ == "__main__":
    main()
