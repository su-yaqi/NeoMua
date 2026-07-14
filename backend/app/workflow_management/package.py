from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from workflow_runtime.package import directory_digest, package_content_digest

from app.workflow_management.models import (
    ConfirmationMode,
    WorkflowNodeType,
    WorkflowProjectMode,
    WorkflowScopeType,
)
from app.workflow_management.sdk import (
    EDGE_CONDITIONS,
    FRONTEND_COMPONENTS,
    NODE_HANDLERS,
    VALIDATORS,
)


class PackageValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("Workflow Package validation failed")
        self.errors = errors


class StrictManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManifestNode(StrictManifestModel):
    key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=255)
    type: WorkflowNodeType
    confirmation_mode: ConfirmationMode
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    runtime_policy: dict[str, Any] = {}
    agent_release_id: str | None = None
    agent_role_key: str | None = Field(
        default=None, min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*$"
    )
    handler_key: str | None = None
    validate_in: str | None = None
    validate_out: str | None = None
    skippable: bool = False
    skip_output: dict[str, Any] | None = None
    side_effecting: bool = False
    join_policy: str = "all"

    @model_validator(mode="after")
    def validate_contract(self) -> "ManifestNode":
        if self.type == WorkflowNodeType.HUMAN and self.handler_key:
            raise ValueError("human nodes cannot declare a handler")
        if self.type == WorkflowNodeType.CODE and not self.handler_key:
            raise ValueError("code nodes require handler_key")
        if self.type == WorkflowNodeType.AGENT:
            if bool(self.agent_release_id) == bool(self.agent_role_key):
                raise ValueError(
                    "agent nodes require exactly one of agent_release_id or agent_role_key"
                )
        elif self.agent_release_id or self.agent_role_key:
            raise ValueError("only agent nodes may declare an Agent binding")
        if (
            self.confirmation_mode == ConfirmationMode.PROCESS
            and self.type != WorkflowNodeType.HUMAN
        ):
            raise ValueError("process_confirmation is only valid for human nodes")
        if self.skippable and self.skip_output is None:
            raise ValueError("skippable nodes require skip_output")
        if self.side_effecting and not self.runtime_policy.get(
            "idempotency_proof_required"
        ):
            raise ValueError("side-effecting nodes require idempotency_proof_required")
        if self.join_policy not in {"all", "any"}:
            raise ValueError("join_policy must be all or any")
        return self


class ManifestEdge(StrictManifestModel):
    source: str
    target: str
    condition_key: str = "always"
    condition_config: dict[str, Any] = {}


class ManifestApplication(StrictManifestModel):
    component_key: str
    route_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    build_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    shell_version: str


class WorkflowManifest(StrictManifestModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    description: str | None = None
    version: str
    sdk_version: str
    scope_type: WorkflowScopeType
    namespace_id: str | None = None
    project_mode: WorkflowProjectMode
    nodes: list[ManifestNode] = Field(min_length=1)
    edges: list[ManifestEdge]
    entry_nodes: list[str] = Field(min_length=1)
    exit_nodes: list[str] = Field(min_length=1)
    application: ManifestApplication
    input_schema: dict[str, Any]
    requirements: dict[str, Any] = {}

    @model_validator(mode="after")
    def validate_scope(self) -> "WorkflowManifest":
        if self.scope_type == WorkflowScopeType.PLATFORM and self.namespace_id:
            raise ValueError("platform packages cannot declare namespace_id")
        if self.scope_type == WorkflowScopeType.NAMESPACE and not self.namespace_id:
            raise ValueError("namespace packages require namespace_id")
        if (
            self.requirements.get("repositories")
            and self.project_mode != WorkflowProjectMode.REQUIRED
        ):
            raise ValueError(
                "workflows requiring repositories must use project_mode=required"
            )
        return self


def canonical_manifest(manifest: WorkflowManifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json", exclude_none=True)


def package_digest(manifest: WorkflowManifest) -> str:
    from app.workflow_management.bundled import bundled_package_root

    package_root = bundled_package_root(manifest.slug)
    return package_content_digest(package_root, canonical_manifest(manifest))


def _schema_errors(schema: dict[str, Any], path: str) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(schema, dict):
        return [
            {
                "path": path,
                "code": "invalid_schema",
                "message": "Schema must be an object",
            }
        ]
    if schema.get("type") not in {
        None,
        "object",
        "array",
        "string",
        "number",
        "integer",
        "boolean",
        "null",
    }:
        errors.append(
            {
                "path": path,
                "code": "invalid_schema_type",
                "message": "Unknown JSON Schema type",
            }
        )
    if schema.get("type") == "object" and "additionalProperties" not in schema:
        errors.append(
            {
                "path": path,
                "code": "unknown_fields_policy_missing",
                "message": "Object schemas must declare additionalProperties",
            }
        )
    return errors


def validate_package(raw: dict[str, Any]) -> WorkflowManifest:
    from app.workflow_management.bundled import load_bundled_workflow_code

    load_bundled_workflow_code()
    try:
        manifest = WorkflowManifest.model_validate(raw)
    except ValidationError as exc:
        raise PackageValidationError(
            [
                {
                    "path": ".".join(str(part) for part in error["loc"]),
                    "code": error["type"],
                    "message": error["msg"],
                }
                for error in exc.errors()
            ]
        ) from exc
    errors: list[dict[str, Any]] = []
    from app.workflow_management.bundled import bundled_package_root

    try:
        package_root = bundled_package_root(manifest.slug)
    except RuntimeError as exc:
        raise PackageValidationError(
            [
                {
                    "path": "slug",
                    "code": "package_source_missing",
                    "message": str(exc),
                }
            ]
        ) from exc
    try:
        frontend_digest = directory_digest(package_root / "frontend")
    except ValueError as exc:
        errors.append(
            {
                "path": "application",
                "code": "frontend_package_missing",
                "message": str(exc),
            }
        )
    else:
        if manifest.application.build_digest != frontend_digest:
            errors.append(
                {
                    "path": "application.build_digest",
                    "code": "frontend_digest_mismatch",
                    "message": frontend_digest,
                }
            )
    node_keys = [node.key for node in manifest.nodes]
    node_set = set(node_keys)
    if len(node_keys) != len(node_set):
        errors.append(
            {
                "path": "nodes",
                "code": "duplicate_node",
                "message": "Node keys must be unique",
            }
        )
    for key in [*manifest.entry_nodes, *manifest.exit_nodes]:
        if key not in node_set:
            errors.append(
                {
                    "path": "entry_nodes/exit_nodes",
                    "code": "unknown_node",
                    "message": key,
                }
            )
    adjacency: dict[str, list[str]] = {key: [] for key in node_keys}
    indegree: dict[str, int] = dict.fromkeys(node_keys, 0)
    edge_pairs: set[tuple[str, str, str]] = set()
    for index, edge in enumerate(manifest.edges):
        if edge.source not in node_set or edge.target not in node_set:
            errors.append(
                {
                    "path": f"edges.{index}",
                    "code": "unknown_node",
                    "message": "Edge references an unknown node",
                }
            )
            continue
        identity = (edge.source, edge.target, edge.condition_key)
        if identity in edge_pairs:
            errors.append(
                {
                    "path": f"edges.{index}",
                    "code": "duplicate_edge",
                    "message": str(identity),
                }
            )
        edge_pairs.add(identity)
        adjacency[edge.source].append(edge.target)
        indegree[edge.target] += 1
        if edge.condition_key not in EDGE_CONDITIONS:
            errors.append(
                {
                    "path": f"edges.{index}.condition_key",
                    "code": "condition_not_registered",
                    "message": edge.condition_key,
                }
            )
    queue = deque(key for key, degree in indegree.items() if degree == 0)
    visited: list[str] = []
    mutable_indegree = dict(indegree)
    while queue:
        key = queue.popleft()
        visited.append(key)
        for target in adjacency[key]:
            mutable_indegree[target] -= 1
            if mutable_indegree[target] == 0:
                queue.append(target)
    if len(visited) != len(node_keys):
        errors.append(
            {
                "path": "edges",
                "code": "dag_cycle",
                "message": "Workflow graph must be acyclic",
            }
        )
    reachable: set[str] = set()
    queue = deque(manifest.entry_nodes)
    while queue:
        key = queue.popleft()
        if key in reachable or key not in adjacency:
            continue
        reachable.add(key)
        queue.extend(adjacency[key])
    for key in node_set - reachable:
        errors.append(
            {"path": f"nodes.{key}", "code": "unreachable_node", "message": key}
        )
    for key in manifest.exit_nodes:
        if adjacency.get(key):
            errors.append(
                {"path": f"exit_nodes.{key}", "code": "exit_has_edges", "message": key}
            )
    for index, node in enumerate(manifest.nodes):
        errors.extend(_schema_errors(node.input_schema, f"nodes.{index}.input_schema"))
        errors.extend(
            _schema_errors(node.output_schema, f"nodes.{index}.output_schema")
        )
        if node.handler_key and node.handler_key not in NODE_HANDLERS:
            errors.append(
                {
                    "path": f"nodes.{index}.handler_key",
                    "code": "handler_not_registered",
                    "message": node.handler_key,
                }
            )
        for field_name, validator_key in (
            ("validate_in", node.validate_in),
            ("validate_out", node.validate_out),
        ):
            if validator_key and validator_key not in VALIDATORS:
                errors.append(
                    {
                        "path": f"nodes.{index}.{field_name}",
                        "code": "validator_not_registered",
                        "message": validator_key,
                    }
                )
    errors.extend(_schema_errors(manifest.input_schema, "input_schema"))
    if manifest.application.component_key not in FRONTEND_COMPONENTS:
        errors.append(
            {
                "path": "application.component_key",
                "code": "component_not_registered",
                "message": manifest.application.component_key,
            }
        )
    if errors:
        raise PackageValidationError(errors)
    return manifest
