from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDefinition:
    name: str
    category: str
    method: str
    path: str
    required_role: str
    confirmation: bool = False
    supports_json: bool = True
    idempotent_mutation: bool = False
    aliases: tuple[str, ...] = ()


COMMANDS = (
    CommandDefinition("auth.login", "auth", "POST", "/cli/login", "anonymous"),
    CommandDefinition("auth.logout", "auth", "POST", "/cli/logout", "developer", True),
    CommandDefinition("auth.status", "auth", "GET", "/users/me", "developer"),
    CommandDefinition("profile.list", "profile", "LOCAL", "profiles", "local"),
    CommandDefinition("profile.use", "profile", "LOCAL", "profiles", "local"),
    CommandDefinition("profile.set-namespace", "profile", "LOCAL", "profiles", "local"),
    CommandDefinition("profile.delete", "profile", "LOCAL", "profiles", "local", True),
    CommandDefinition("agents.list", "agents", "GET", "/agents", "developer"),
    CommandDefinition("agents.get", "agents", "GET", "/agents/{id}", "developer"),
    CommandDefinition(
        "agents.validate", "agents", "POST", "/agents/{id}/draft/validate", "admin"
    ),
    CommandDefinition(
        "agents.release",
        "agents",
        "POST",
        "/agents/{id}/releases",
        "admin",
        True,
        True,
        True,
    ),
    CommandDefinition("skills.list", "skills", "GET", "/skills", "developer"),
    CommandDefinition("skills.get", "skills", "GET", "/skills/{id}", "developer"),
    CommandDefinition(
        "skills.upload",
        "skills",
        "POST",
        "/skills/{id}/versions",
        "admin",
        False,
        True,
        True,
    ),
    CommandDefinition(
        "skills.deprecate",
        "skills",
        "POST",
        "/skills/{id}/versions/{version}/deprecate",
        "admin",
        True,
    ),
    CommandDefinition("tools.list", "tools", "GET", "/tools/catalog", "developer"),
    CommandDefinition("mcp.list", "mcp", "GET", "/mcp-servers", "developer"),
    CommandDefinition("mcp.get", "mcp", "GET", "/mcp-servers/{id}", "developer"),
    CommandDefinition(
        "mcp.validate",
        "mcp",
        "POST",
        "/mcp-targets/{id}/validate",
        "admin",
        False,
        True,
        True,
    ),
    CommandDefinition("plugins.list", "plugins", "GET", "/plugins", "developer"),
    CommandDefinition("plugins.get", "plugins", "GET", "/plugins/{id}", "developer"),
    CommandDefinition(
        "plugins.validate", "plugins", "POST", "/plugins/{id}/draft/validate", "admin"
    ),
    CommandDefinition(
        "plugins.publish",
        "plugins",
        "POST",
        "/plugins/{id}/versions",
        "admin",
        True,
        True,
        True,
    ),
    CommandDefinition(
        "releases.get", "releases", "GET", "/agent-releases/{id}", "developer"
    ),
    CommandDefinition(
        "releases.activate",
        "releases",
        "POST",
        "/agent-releases/{id}/activations",
        "admin",
        True,
        True,
        True,
    ),
    CommandDefinition(
        "releases.retry",
        "releases",
        "POST",
        "/agent-deployments/{id}/retry",
        "admin",
        True,
        True,
        True,
    ),
    CommandDefinition(
        "releases.rollback",
        "releases",
        "POST",
        "/agent-deployments/{id}/rollback",
        "admin",
        True,
        True,
        True,
    ),
    CommandDefinition(
        "runtimes.list", "runtimes", "GET", "/runtime-agents", "developer"
    ),
    CommandDefinition(
        "runtimes.get", "runtimes", "GET", "/runtime-agents/{id}", "developer"
    ),
    CommandDefinition(
        "tasks.create",
        "tasks",
        "POST",
        "/runtime-tasks",
        "developer",
        False,
        True,
        True,
    ),
    CommandDefinition("tasks.get", "tasks", "GET", "/runtime-tasks/{id}", "developer"),
    CommandDefinition(
        "tasks.events", "tasks", "GET", "/runtime-tasks/{id}/events", "developer"
    ),
    CommandDefinition(
        "tasks.cancel", "tasks", "POST", "/runtime-tasks/{id}/cancel", "developer", True
    ),
    CommandDefinition(
        "tasks.retry",
        "tasks",
        "POST",
        "/runtime-tasks/{id}/retry",
        "developer",
        True,
        True,
        True,
    ),
    CommandDefinition(
        "tasks.approvals", "tasks", "GET", "/runtime-tasks/{id}/approvals", "developer"
    ),
    CommandDefinition(
        "approvals.approve",
        "approvals",
        "POST",
        "/tool-approvals/{id}/approve",
        "developer",
        True,
    ),
    CommandDefinition(
        "approvals.deny",
        "approvals",
        "POST",
        "/tool-approvals/{id}/deny",
        "developer",
        True,
    ),
    CommandDefinition("node.secret.set", "node", "LOCAL", "keychain", "local"),
    CommandDefinition("node.secret.list", "node", "LOCAL", "keychain", "local"),
    CommandDefinition("node.secret.remove", "node", "LOCAL", "keychain", "local", True),
    CommandDefinition("node.secret.status", "node", "LOCAL", "keychain", "local"),
)


def validate_registry() -> None:
    names = [command.name for command in COMMANDS]
    if len(names) != len(set(names)):
        raise RuntimeError("Operator CLI command registry contains duplicate names")
    aliases = [alias for command in COMMANDS for alias in command.aliases]
    if len(aliases) != len(set(aliases)) or set(aliases) & set(names):
        raise RuntimeError("Operator CLI command registry contains conflicting aliases")


validate_registry()
