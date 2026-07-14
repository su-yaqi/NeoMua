import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.agent_management.capability_models import AgentRelease, RuntimeAgentRelease
from app.conversation_management.models import (
    Conversation,
    ConversationAgentRole,
    ConversationMessage,
    ConversationMode,
    ConversationVisibility,
    MessageAuthorType,
    MessageStatus,
    MessageTargetType,
)
from app.conversation_management.service import (
    add_conversation_agent,
    canonical_digest,
    create_agent_task,
    next_message_sequence,
)
from app.project_management.models import (
    Project,
    ProjectRepository,
    ProjectSpecBinding,
    ProjectSpecLocation,
    ProjectStatus,
    RepositoryStatus,
    SpecBindingStatus,
    SpecLocationStatus,
    SpecStandard,
    SpecStandardVersion,
)
from app.project_management.service import verified_repository_runtime_proof
from app.runtime.jobs import enqueue_runtime_job
from app.runtime.models import (
    AgentTask,
    RuntimeJob,
    RuntimeJobKind,
    RuntimeJobStatus,
    RuntimeProfile,
)
from app.runtime.policy import TaskStatus
from app.workflow_management.models import (
    ConfirmationDecision,
    ConfirmationMode,
    GateType,
    NamespaceWorkflowEnablement,
    WorkflowConfirmation,
    WorkflowContextMode,
    WorkflowEdgeDefinition,
    WorkflowEvent,
    WorkflowExecutionStatus,
    WorkflowGateResult,
    WorkflowInstance,
    WorkflowInstanceStatus,
    WorkflowNodeDefinition,
    WorkflowNodeExecution,
    WorkflowNodeInstance,
    WorkflowNodeRevision,
    WorkflowNodeStatus,
    WorkflowNodeType,
    WorkflowProjectMode,
    WorkflowTemplateVersion,
    WorkflowVersionStatus,
)
from app.workflow_management.sdk import (
    EDGE_CONDITIONS,
    WorkflowRunContext,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_json_value(
    value: Any, schema: dict[str, Any], path: str = "$"
) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    type_matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if expected and expected in type_matches and not type_matches[expected]:
        return [f"{path}: expected {expected}"]
    if isinstance(value, dict) and expected == "object":
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required")
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{path}.{key}: unknown field")
        for key, item in value.items():
            if key in properties:
                errors.extend(
                    validate_json_value(item, properties[key], f"{path}.{key}")
                )
    if isinstance(value, list) and expected == "array" and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(
                validate_json_value(item, schema["items"], f"{path}[{index}]")
            )
    return errors


def append_event(
    session: Session,
    instance_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> WorkflowEvent:
    last = session.exec(
        select(func.max(WorkflowEvent.sequence)).where(
            WorkflowEvent.workflow_instance_id == instance_id
        )
    ).one()
    event = WorkflowEvent(
        workflow_instance_id=instance_id,
        sequence=int(last or 0) + 1,
        event_type=event_type,
        payload=payload,
    )
    session.add(event)
    return event


def get_instance(
    session: Session, instance_id: uuid.UUID, namespace_id: uuid.UUID
) -> WorkflowInstance:
    instance = session.get(WorkflowInstance, instance_id)
    if instance is None or instance.namespace_id != namespace_id:
        raise HTTPException(404, "Workflow project task not found")
    return instance


def get_node(
    session: Session, instance_id: uuid.UUID, node_key: str
) -> tuple[WorkflowNodeInstance, WorkflowNodeDefinition]:
    node = session.exec(
        select(WorkflowNodeInstance).where(
            WorkflowNodeInstance.workflow_instance_id == instance_id,
            WorkflowNodeInstance.node_key == node_key,
        )
    ).first()
    if node is None:
        raise HTTPException(404, "Workflow node not found")
    definition = session.get(WorkflowNodeDefinition, node.node_definition_id)
    if definition is None:
        raise HTTPException(409, "Workflow node definition is unavailable")
    return node, definition


def require_mutable_instance(instance: WorkflowInstance) -> None:
    if instance.status in {
        WorkflowInstanceStatus.COMPLETED,
        WorkflowInstanceStatus.CANCELLED,
    }:
        raise HTTPException(409, "Completed or cancelled project tasks are read-only")


def require_expected_revision(node: WorkflowNodeInstance, expected: int) -> None:
    if node.expected_revision != expected:
        raise HTTPException(
            409,
            {
                "code": "workflow_revision_conflict",
                "expected_revision": expected,
                "current_revision": node.expected_revision,
            },
        )


def _definitions(
    session: Session, version_id: uuid.UUID
) -> tuple[list[WorkflowNodeDefinition], list[WorkflowEdgeDefinition]]:
    nodes = session.exec(
        select(WorkflowNodeDefinition)
        .where(WorkflowNodeDefinition.template_version_id == version_id)
        .order_by(col(WorkflowNodeDefinition.position))
    ).all()
    edges = session.exec(
        select(WorkflowEdgeDefinition).where(
            WorkflowEdgeDefinition.template_version_id == version_id
        )
    ).all()
    return list(nodes), list(edges)


def _runtime_capability_errors(
    runtime: RuntimeProfile, policy: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    requirements = policy.get("requirements", {})
    capabilities = {**runtime.config, **runtime.harness_capabilities}
    for key, required in requirements.items():
        actual = capabilities.get(key)
        if isinstance(required, bool):
            if required and not actual:
                errors.append(f"Runtime capability {key} is required")
        elif isinstance(required, list):
            actual_values = set(actual or [])
            missing = [value for value in required if value not in actual_values]
            if missing:
                errors.append(f"Runtime capability {key} is missing {missing}")
        elif actual != required:
            errors.append(f"Runtime capability {key} must equal {required!r}")
    return errors


def project_context_snapshot(session: Session, project: Project) -> dict[str, Any]:
    repositories = session.exec(
        select(ProjectRepository).where(ProjectRepository.project_id == project.id)
    ).all()
    if not repositories:
        raise HTTPException(409, "Project has no repository context")
    unavailable = [
        str(item.id)
        for item in repositories
        if item.status != RepositoryStatus.AVAILABLE or not item.validated_commit
    ]
    if unavailable:
        raise HTTPException(
            409,
            {"code": "project_repository_unavailable", "repository_ids": unavailable},
        )
    repository_refs = [
        {
            "repository_id": str(item.id),
            "remote_url": item.remote_url,
            "purpose": item.purpose,
            "commit": item.validated_commit,
            "runtime_workspace_refs": item.runtime_workspace_refs,
        }
        for item in repositories
    ]
    spec_refs: list[dict[str, Any]] = []
    content_refs: list[dict[str, Any]] = []
    locations = session.exec(
        select(ProjectSpecLocation).where(ProjectSpecLocation.project_id == project.id)
    ).all()
    for location in locations:
        binding = session.exec(
            select(ProjectSpecBinding).where(
                ProjectSpecBinding.spec_location_id == location.id
            )
        ).first()
        version = (
            session.get(SpecStandardVersion, binding.standard_version_id)
            if binding
            else None
        )
        standard = session.get(SpecStandard, version.standard_id) if version else None
        repository = next(
            item for item in repositories if item.id == location.repository_id
        )
        if (
            binding is None
            or version is None
            or standard is None
            or binding.status != SpecBindingStatus.VALID
            or location.status != SpecLocationStatus.VALID
            or binding.validated_commit != repository.validated_commit
        ):
            raise HTTPException(
                409,
                {"code": "spec_binding_unavailable", "location_id": str(location.id)},
            )
        spec_refs.append(
            {
                "location_id": str(location.id),
                "repository_id": str(repository.id),
                "path": location.path,
                "commit": repository.validated_commit,
                "standard": standard.slug,
                "standard_version": version.version,
                "standard_version_id": str(version.id),
                "content_digest": version.content_digest,
            }
        )
        seen_files: set[tuple[str, str]] = set()
        for runtime_id, proof in repository.runtime_workspace_refs.items():
            if not isinstance(proof, dict):
                continue
            for proof_location in proof.get("spec_locations", []):
                if str(proof_location.get("spec_location_id")) != str(location.id):
                    continue
                for file in proof_location.get("files", []):
                    identity = (str(repository.id), str(file.get("path")))
                    if identity in seen_files:
                        continue
                    seen_files.add(identity)
                    content_refs.append(
                        {
                            "repository_id": str(repository.id),
                            "spec_location_id": str(location.id),
                            "runtime_id": runtime_id,
                            "path": file["path"],
                            "blob_digest": file["content_digest"],
                            "size": file["size"],
                            "content": file["content"],
                        }
                    )
    snapshot = {
        "context_mode": WorkflowContextMode.PROJECT.value,
        "project_id": str(project.id),
        "repository_refs": repository_refs,
        "spec_refs": spec_refs,
        "content_refs": content_refs,
    }
    return {**snapshot, "content_digest": canonical_digest(snapshot)}


def preflight(
    session: Session,
    namespace_id: uuid.UUID,
    project: Project | None,
    version: WorkflowTemplateVersion,
    node_bindings: dict[str, dict[str, uuid.UUID | None]],
    *,
    require_enabled: bool = True,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    raw_project_mode = version.manifest.get("project_mode")
    try:
        project_mode = WorkflowProjectMode(
            raw_project_mode or WorkflowProjectMode.REQUIRED.value
        )
    except ValueError:
        project_mode = None
        errors.append(
            {
                "code": "template_project_mode_invalid",
                "message": str(raw_project_mode),
            }
        )
    if project_mode == WorkflowProjectMode.REQUIRED and project is None:
        errors.append(
            {
                "code": "project_required",
                "message": "This Workflow version requires a project",
            }
        )
    if project_mode == WorkflowProjectMode.NONE and project is not None:
        errors.append(
            {
                "code": "project_not_allowed",
                "message": "This Workflow version does not accept project context",
            }
        )
    if project is not None and project.status == ProjectStatus.ARCHIVED:
        errors.append({"code": "project_archived", "message": "Project is archived"})
    if version.status != WorkflowVersionStatus.ACTIVE:
        errors.append(
            {"code": "template_version_unavailable", "message": version.status.value}
        )
    if require_enabled:
        enablement = session.exec(
            select(NamespaceWorkflowEnablement).where(
                NamespaceWorkflowEnablement.namespace_id == namespace_id,
                NamespaceWorkflowEnablement.template_version_id == version.id,
                col(NamespaceWorkflowEnablement.enabled).is_(True),
            )
        ).first()
        if enablement is None:
            errors.append(
                {
                    "code": "template_version_not_enabled",
                    "message": "Version is not enabled",
                }
            )
    standalone_context = {"context_mode": WorkflowContextMode.STANDALONE.value}
    context_snapshot: dict[str, Any] = {
        **standalone_context,
        "content_digest": canonical_digest(standalone_context),
    }
    if project is not None:
        try:
            context_snapshot = project_context_snapshot(session, project)
        except HTTPException as exc:
            errors.append({"code": "project_context_invalid", "message": exc.detail})
    definitions, _ = _definitions(session, version.id)
    required_node_keys = {
        definition.node_key
        for definition in definitions
        if definition.node_type != WorkflowNodeType.HUMAN
    }
    for node_key in sorted(required_node_keys - node_bindings.keys()):
        errors.append({"code": "workflow_node_unconfigured", "node_key": node_key})
    for node_key in sorted(node_bindings.keys() - required_node_keys):
        errors.append({"code": "workflow_node_binding_unknown", "node_key": node_key})
    runtime_resolution: dict[str, Any] = {}
    resolved_agent_bindings: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        if definition.node_type == WorkflowNodeType.HUMAN:
            runtime_resolution[definition.node_key] = {}
            continue
        node_binding = node_bindings.get(definition.node_key)
        if node_binding is None:
            continue
        runtime_id = node_binding.get("runtime_id")
        if runtime_id is None:
            errors.append(
                {"code": "runtime_unresolved", "node_key": definition.node_key}
            )
            continue
        runtime = session.get(RuntimeProfile, runtime_id)
        if runtime is None or runtime.namespace_id != namespace_id:
            errors.append(
                {"code": "runtime_unavailable", "node_key": definition.node_key}
            )
            continue
        capability_errors = _runtime_capability_errors(
            runtime, definition.runtime_policy
        )
        for message in capability_errors:
            errors.append(
                {
                    "code": "runtime_incompatible",
                    "node_key": definition.node_key,
                    "message": message,
                }
            )
        if (
            version.manifest.get("requirements", {}).get("repositories")
            and project is not None
        ):
            repositories = session.exec(
                select(ProjectRepository).where(
                    ProjectRepository.project_id == project.id
                )
            ).all()
            for repository in repositories:
                proof = verified_repository_runtime_proof(
                    session, repository, runtime.id
                )
                if proof is None:
                    errors.append(
                        {
                            "code": "runtime_repository_unverified",
                            "node_key": definition.node_key,
                            "repository_id": str(repository.id),
                            "runtime_id": str(runtime.id),
                        }
                    )
        configured_release_id = node_binding.get("agent_release_id")
        if definition.node_type == WorkflowNodeType.AGENT:
            if (
                definition.agent_release_id is not None
                and configured_release_id is not None
                and configured_release_id != definition.agent_release_id
            ):
                errors.append(
                    {
                        "code": "agent_release_fixed_by_package",
                        "node_key": definition.node_key,
                    }
                )
                continue
            release_id = definition.agent_release_id or configured_release_id
            if release_id is None:
                errors.append(
                    {
                        "code": "agent_release_unconfigured",
                        "node_key": definition.node_key,
                    }
                )
                continue
            binding = session.exec(
                select(RuntimeAgentRelease).where(
                    RuntimeAgentRelease.runtime_profile_id == runtime.id,
                    RuntimeAgentRelease.current_release_id == release_id,
                )
            ).first()
            release = session.get(AgentRelease, release_id)
            if (
                binding is None
                or release is None
                or release.namespace_id != namespace_id
                or binding.applied_digest != release.resolved_spec_digest
                or binding.materialization_digest != release.resolved_spec_digest
            ):
                errors.append(
                    {
                        "code": "agent_release_not_active",
                        "node_key": definition.node_key,
                        "role_key": definition.agent_role_key,
                    }
                )
            else:
                runtime_resolution[definition.node_key] = {
                    "runtime_id": str(runtime.id),
                    "agent_release_id": str(release.id),
                    "resolved_spec_digest": release.resolved_spec_digest,
                }
                role_key = definition.agent_role_key or definition.node_key
                resolved = {
                    "node_key": definition.node_key,
                    "role_key": role_key,
                    "runtime_id": str(runtime.id),
                    "agent_release_id": str(release.id),
                    "agent_id": str(release.agent_id),
                    "release_version": release.version,
                    "resolved_spec_digest": release.resolved_spec_digest,
                }
                previous_binding = resolved_agent_bindings.get(role_key)
                if previous_binding is not None and previous_binding != resolved:
                    errors.append(
                        {
                            "code": "agent_role_resolution_conflict",
                            "role_key": role_key,
                        }
                    )
                else:
                    resolved_agent_bindings[role_key] = resolved
                continue
        elif configured_release_id is not None:
            errors.append(
                {
                    "code": "agent_release_not_allowed",
                    "node_key": definition.node_key,
                }
            )
            continue
        runtime_resolution[definition.node_key] = {"runtime_id": str(runtime.id)}
    return {
        "passed": not errors,
        "errors": errors,
        "runtime_resolution": runtime_resolution,
        "agent_bindings": resolved_agent_bindings,
        "project_context_snapshot": context_snapshot,
        "context_mode": (
            WorkflowContextMode.PROJECT
            if project is not None
            else WorkflowContextMode.STANDALONE
        ),
        "package_digest": version.package_digest,
    }


def _incoming_edges(
    edges: list[WorkflowEdgeDefinition], node_key: str
) -> list[WorkflowEdgeDefinition]:
    return [edge for edge in edges if edge.target_node_key == node_key]


def _outgoing_edges(
    edges: list[WorkflowEdgeDefinition], node_key: str
) -> list[WorkflowEdgeDefinition]:
    return [edge for edge in edges if edge.source_node_key == node_key]


def _node_map(
    session: Session, instance_id: uuid.UUID
) -> dict[str, WorkflowNodeInstance]:
    return {
        node.node_key: node
        for node in session.exec(
            select(WorkflowNodeInstance).where(
                WorkflowNodeInstance.workflow_instance_id == instance_id
            )
        ).all()
    }


def _current_revision(
    session: Session, node: WorkflowNodeInstance
) -> WorkflowNodeRevision | None:
    if node.current_revision_id is None:
        return None
    return session.get(WorkflowNodeRevision, node.current_revision_id)


def build_node_input(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    edges: list[WorkflowEdgeDefinition],
) -> dict[str, Any]:
    nodes = _node_map(session, instance.id)
    upstream: dict[str, Any] = {}
    revisions: dict[str, int] = {}
    for edge in _incoming_edges(edges, node.node_key):
        source = nodes[edge.source_node_key]
        revision = _current_revision(session, source)
        if revision is None:
            continue
        upstream[source.node_key] = {
            "status": source.status.value,
            "output": revision.output,
            "skipped": revision.skipped,
        }
        revisions[source.node_key] = revision.revision
    return {
        "task_input": instance.input,
        "upstream": upstream,
        "upstream_revisions": revisions,
        "project_context": instance.project_context_snapshot,
    }


def _run_context(
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    input_snapshot: dict[str, Any],
    previous: WorkflowNodeRevision | None,
    idempotency_key: str,
) -> WorkflowRunContext:
    return WorkflowRunContext(
        workflow_instance_id=str(instance.id),
        node_key=node.node_key,
        runtime_id=str(node.resolved_runtime_id),
        project_id=str(instance.project_id) if instance.project_id else None,
        input=input_snapshot,
        previous_output=previous.output if previous else None,
        change_summary={
            "previous_input_digest": previous.input_digest if previous else None,
            "new_input_digest": canonical_digest(input_snapshot),
        },
        idempotency_key=idempotency_key,
    )


def _record_gate_result(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    gate_type: GateType,
    validator_key: str,
    input_digest: str,
    revision: int,
    passed: bool,
    details: dict[str, Any],
) -> WorkflowGateResult:
    result = WorkflowGateResult(
        node_instance_id=node.id,
        revision=revision,
        gate_type=gate_type,
        validator_key=validator_key,
        validator_version=instance.package_digest,
        input_digest=input_digest,
        passed=passed,
        details=details,
    )
    session.add(result)
    return result


def _runtime_context_payload(context: WorkflowRunContext) -> dict[str, Any]:
    return {
        "workflow_instance_id": context.workflow_instance_id,
        "node_key": context.node_key,
        "runtime_id": context.runtime_id,
        "project_id": context.project_id,
        "input": context.input,
        "previous_output": context.previous_output,
        "change_summary": context.change_summary,
        "idempotency_key": context.idempotency_key,
    }


def _queue_runtime_component(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    input_snapshot: dict[str, Any],
    previous: WorkflowNodeRevision | None,
    *,
    phase: str,
    kind: RuntimeJobKind,
    component_key: str,
    pending_payload: dict[str, Any] | None = None,
    side_effecting: bool = False,
) -> WorkflowNodeExecution:
    execution = _new_execution(
        session,
        node,
        node.expected_revision + 1,
        phase=phase,
        status=WorkflowExecutionStatus.QUEUED,
    )
    execution.pending_payload = pending_payload or {}
    context = _run_context(
        instance,
        node,
        input_snapshot,
        previous,
        execution.idempotency_key,
    )
    template_version = session.get(
        WorkflowTemplateVersion, instance.template_version_id
    )
    if template_version is None:
        raise HTTPException(409, "Workflow Template Version is unavailable")
    package_slug = template_version.manifest.get("slug")
    if not isinstance(package_slug, str) or not package_slug:
        raise HTTPException(409, "Workflow Package slug is unavailable")
    job = enqueue_runtime_job(
        session,
        namespace_id=instance.namespace_id,
        runtime_id=node.resolved_runtime_id,
        kind=kind,
        payload={
            "component_key": component_key,
            "context": _runtime_context_payload(context),
            "package_digest": instance.package_digest,
            "package_slug": package_slug,
        },
        idempotency_key=execution.idempotency_key,
        side_effecting=side_effecting,
    )
    execution.runtime_job_id = job.id
    node.status = WorkflowNodeStatus.RUNNING
    session.add_all([execution, node])
    return execution


def append_revision(
    session: Session,
    node: WorkflowNodeInstance,
    input_snapshot: dict[str, Any],
    output: dict[str, Any] | None,
    reason: str,
    user_id: uuid.UUID | None,
    *,
    skipped: bool = False,
) -> WorkflowNodeRevision:
    revision_number = node.expected_revision + 1
    revision = WorkflowNodeRevision(
        node_instance_id=node.id,
        revision=revision_number,
        input_snapshot=input_snapshot,
        input_digest=canonical_digest(input_snapshot),
        output=output,
        output_digest=canonical_digest(output) if output is not None else None,
        change_reason=reason,
        skipped=skipped,
        created_by=user_id,
    )
    session.add(revision)
    session.flush()
    node.expected_revision = revision_number
    node.current_revision_id = revision.id
    node.latest_input_digest = revision.input_digest
    node.updated_at = utcnow()
    session.add(node)
    return revision


def _can_activate(
    session: Session,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    edges: list[WorkflowEdgeDefinition],
    nodes: dict[str, WorkflowNodeInstance],
) -> bool:
    incoming = _incoming_edges(edges, node.node_key)
    if not incoming:
        return True
    active_sources = 0
    satisfied_sources = 0
    for edge in incoming:
        source = nodes[edge.source_node_key]
        revision = _current_revision(session, source)
        if (
            source.status
            not in {WorkflowNodeStatus.COMPLETED, WorkflowNodeStatus.SKIPPED}
            or revision is None
        ):
            continue
        condition = EDGE_CONDITIONS.get(edge.condition_key)
        if condition is None:
            continue
        active_sources += 1
        if condition(revision.output or {}, edge.condition_config):
            satisfied_sources += 1
    if definition.join_policy == "any":
        return satisfied_sources > 0
    return active_sources == len(incoming) and satisfied_sources == len(incoming)


def _new_execution(
    session: Session,
    node: WorkflowNodeInstance,
    input_revision: int,
    *,
    phase: str = "run",
    status: WorkflowExecutionStatus = WorkflowExecutionStatus.RUNNING,
) -> WorkflowNodeExecution:
    latest_attempt = session.exec(
        select(func.max(WorkflowNodeExecution.attempt)).where(
            WorkflowNodeExecution.node_instance_id == node.id
        )
    ).one()
    attempt = int(latest_attempt or 0) + 1
    execution = WorkflowNodeExecution(
        node_instance_id=node.id,
        input_revision=input_revision,
        attempt=attempt,
        runtime_id=node.resolved_runtime_id,
        status=status,
        phase=phase,
        idempotency_key=f"workflow:{node.workflow_instance_id}:node:{node.node_key}:revision:{input_revision}:attempt:{attempt}:{phase}",
    )
    session.add(execution)
    session.flush()
    return execution


def _execute_code_node(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    input_snapshot: dict[str, Any],
    previous: WorkflowNodeRevision | None,
) -> None:
    if not definition.handler_key:
        node.status = WorkflowNodeStatus.FAILED
        append_event(
            session,
            instance.id,
            "node_failed",
            {"node_key": node.node_key, "code": "handler_not_registered"},
        )
        return
    _queue_runtime_component(
        session,
        instance,
        node,
        input_snapshot,
        previous,
        phase="run",
        kind=RuntimeJobKind.WORKFLOW_HANDLER,
        component_key=definition.handler_key,
        side_effecting=definition.side_effecting,
    )
    append_event(
        session,
        instance.id,
        "code_node_queued",
        {"node_key": node.node_key, "runtime_id": str(node.resolved_runtime_id)},
    )


def _execute_agent_node(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    input_snapshot: dict[str, Any],
    previous: WorkflowNodeRevision | None,
) -> None:
    resolution = instance.runtime_resolution.get(node.node_key, {})
    raw_release_id = resolution.get("agent_release_id")
    try:
        release_id = uuid.UUID(str(raw_release_id)) if raw_release_id else None
    except ValueError:
        release_id = None
    release = session.get(AgentRelease, release_id) if release_id else None
    binding = session.exec(
        select(RuntimeAgentRelease).where(
            RuntimeAgentRelease.runtime_profile_id == node.resolved_runtime_id,
            RuntimeAgentRelease.current_release_id == release_id,
        )
    ).first()
    if (
        release is None
        or binding is None
        or binding.applied_digest != release.resolved_spec_digest
    ):
        node.status = WorkflowNodeStatus.BLOCKED
        instance.status = WorkflowInstanceStatus.BLOCKED
        append_event(
            session,
            instance.id,
            "node_blocked",
            {"node_key": node.node_key, "code": "agent_release_not_active"},
        )
        return
    previous_execution = session.exec(
        select(WorkflowNodeExecution)
        .where(
            WorkflowNodeExecution.node_instance_id == node.id,
            col(WorkflowNodeExecution.conversation_id).is_not(None),
        )
        .order_by(col(WorkflowNodeExecution.attempt).desc())
    ).first()
    conversation = (
        session.get(Conversation, previous_execution.conversation_id)
        if previous_execution and previous_execution.conversation_id
        else None
    )
    if conversation is None:
        if instance.created_by is None:
            node.status = WorkflowNodeStatus.BLOCKED
            instance.status = WorkflowInstanceStatus.BLOCKED
            append_event(
                session,
                instance.id,
                "node_blocked",
                {"node_key": node.node_key, "code": "task_creator_unavailable"},
            )
            return
        creator_id = instance.created_by
        conversation_key = f"workflow:{instance.id}:node:{node.node_key}:conversation"
        conversation = Conversation(
            namespace_id=instance.namespace_id,
            creator_id=creator_id,
            project_id=instance.project_id,
            title=f"{instance.title} / {definition.name}",
            mode=ConversationMode.AGENT,
            visibility=ConversationVisibility.PRIVATE,
            runtime_id=node.resolved_runtime_id,
            idempotency_key=conversation_key,
            creation_fingerprint=canonical_digest(
                {
                    "operation": "workflow_agent_conversation",
                    "workflow_instance_id": str(instance.id),
                    "node_key": node.node_key,
                }
            ),
        )
        session.add(conversation)
        session.flush()
        participant = add_conversation_agent(
            session,
            conversation,
            binding,
            release,
            ConversationAgentRole.MAIN,
            creator_id,
        )
    else:
        from app.conversation_management.models import ConversationAgent

        participant = session.exec(
            select(ConversationAgent).where(
                ConversationAgent.conversation_id == conversation.id,
                ConversationAgent.role == ConversationAgentRole.MAIN,
            )
        ).one()
    execution = _new_execution(session, node, node.expected_revision + 1)
    execution.conversation_id = conversation.id
    prompt = (
        "上游内容已更新，请结合更新重新完成当前节点。\n"
        if previous is not None
        else "请完成当前 Workflow 节点。\n"
    )
    prompt += (
        f"节点输入：{input_snapshot}\n"
        f"请将最终结果提交为符合以下 JSON Schema 的 JSON 对象："
        f"{definition.output_schema}。"
    )
    if definition.side_effecting:
        prompt += (
            "\n此节点会修改外部状态。执行前必须确认工具调用经过授权且具备幂等性；"
            "最终结果还必须包含非空的 `_external_state_proof` 对象，记录幂等键、"
            "目标环境和可核验的执行结果。该字段只用于执行安全审计，不属于业务输出。"
        )
    message = ConversationMessage(
        conversation_id=conversation.id,
        sequence=next_message_sequence(session, conversation.id),
        author_type=MessageAuthorType.SYSTEM,
        target_type=MessageTargetType.MAIN,
        payload={
            "content": prompt,
            "workflow_instance_id": str(instance.id),
            "node_key": node.node_key,
            "previous_input_digest": previous.input_digest if previous else None,
            "new_input_digest": canonical_digest(input_snapshot),
        },
        status=MessageStatus.RUNNING,
        idempotency_key=execution.idempotency_key,
    )
    session.add(message)
    session.flush()
    task = create_agent_task(session, conversation, participant, message, prompt)
    message.task_id = task.id
    execution.agent_task_id = task.id
    node.status = WorkflowNodeStatus.RUNNING
    session.add_all([message, execution, node])
    append_event(
        session,
        instance.id,
        "agent_node_started",
        {
            "node_key": node.node_key,
            "execution_id": str(execution.id),
            "agent_task_id": str(task.id),
            "conversation_id": str(conversation.id),
        },
    )


def _continue_after_entry_gate(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    input_snapshot: dict[str, Any],
    previous: WorkflowNodeRevision | None,
) -> None:
    if definition.confirmation_mode == ConfirmationMode.PROCESS:
        node.status = WorkflowNodeStatus.WAITING_CONFIRMATION
        instance.status = WorkflowInstanceStatus.WAITING
    elif definition.node_type == WorkflowNodeType.CODE:
        _execute_code_node(
            session, instance, node, definition, input_snapshot, previous
        )
    elif definition.node_type == WorkflowNodeType.AGENT:
        _execute_agent_node(
            session, instance, node, definition, input_snapshot, previous
        )
    else:
        node.status = WorkflowNodeStatus.BLOCKED
        instance.status = WorkflowInstanceStatus.BLOCKED
    session.add_all([node, instance])


def _complete_after_exit_gate(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    revision: WorkflowNodeRevision,
) -> None:
    if definition.confirmation_mode == ConfirmationMode.RESULT:
        node.status = WorkflowNodeStatus.WAITING_CONFIRMATION
        instance.status = WorkflowInstanceStatus.WAITING
    else:
        node.status = WorkflowNodeStatus.COMPLETED
    append_event(
        session,
        instance.id,
        "node_output_created",
        {"node_key": node.node_key, "revision": revision.revision},
    )
    session.add_all([node, instance])


def _start_exit_gate(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    input_snapshot: dict[str, Any],
    previous: WorkflowNodeRevision | None,
    revision: WorkflowNodeRevision,
) -> None:
    if not definition.exit_validator_key:
        _complete_after_exit_gate(session, instance, node, definition, revision)
        return
    gate_input = {"input": input_snapshot, "output": revision.output or {}}
    _queue_runtime_component(
        session,
        instance,
        node,
        gate_input,
        previous,
        phase="exit_gate",
        kind=RuntimeJobKind.WORKFLOW_VALIDATOR,
        component_key=definition.exit_validator_key,
        pending_payload={
            "revision_id": str(revision.id),
            "gate_input_digest": canonical_digest(gate_input),
        },
    )


def activate_node(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    edges: list[WorkflowEdgeDefinition],
) -> None:
    input_snapshot = build_node_input(session, instance, node, edges)
    input_digest = canonical_digest(input_snapshot)
    previous = _current_revision(session, node)
    if (
        previous
        and previous.input_digest == input_digest
        and node.status == WorkflowNodeStatus.COMPLETED
    ):
        return
    schema_errors = validate_json_value(input_snapshot, definition.input_schema)
    if schema_errors:
        node.status = WorkflowNodeStatus.BLOCKED
        instance.status = WorkflowInstanceStatus.BLOCKED
        append_event(
            session,
            instance.id,
            "node_blocked",
            {"node_key": node.node_key, "schema_errors": schema_errors},
        )
        return
    node.status = WorkflowNodeStatus.READY
    if definition.entry_validator_key:
        _queue_runtime_component(
            session,
            instance,
            node,
            input_snapshot,
            previous,
            phase="entry_gate",
            kind=RuntimeJobKind.WORKFLOW_VALIDATOR,
            component_key=definition.entry_validator_key,
            pending_payload={"gate_input_digest": input_digest},
        )
        session.add_all([node, instance])
        return
    _continue_after_entry_gate(
        session, instance, node, definition, input_snapshot, previous
    )


def advance_instance(session: Session, instance: WorkflowInstance) -> None:
    if instance.status in {
        WorkflowInstanceStatus.COMPLETED,
        WorkflowInstanceStatus.CANCELLED,
        WorkflowInstanceStatus.FAILED,
    }:
        return
    definitions, edges = _definitions(session, instance.template_version_id)
    while True:
        nodes = _node_map(session, instance.id)
        progressed = False
        for definition in definitions:
            node = nodes[definition.node_key]
            if node.status not in {
                WorkflowNodeStatus.INACTIVE,
                WorkflowNodeStatus.UPDATE_REQUIRED,
                WorkflowNodeStatus.READY,
            }:
                continue
            if _can_activate(session, node, definition, edges, nodes):
                before = node.status
                activate_node(session, instance, node, definition, edges)
                if node.status != before:
                    progressed = True
        session.flush()
        if not progressed:
            break
    nodes = _node_map(session, instance.id)
    exit_keys = set(instance_version_exit_nodes(session, instance.template_version_id))
    if exit_keys and all(
        nodes[key].status in {WorkflowNodeStatus.COMPLETED, WorkflowNodeStatus.SKIPPED}
        for key in exit_keys
    ):
        instance.status = WorkflowInstanceStatus.COMPLETED
        instance.completed_at = utcnow()
        append_event(session, instance.id, "task_completed", {})
    elif any(node.status == WorkflowNodeStatus.FAILED for node in nodes.values()):
        instance.status = WorkflowInstanceStatus.FAILED
    elif any(
        node.status
        in {
            WorkflowNodeStatus.BLOCKED,
            WorkflowNodeStatus.NEEDS_MANUAL_RESOLUTION,
        }
        for node in nodes.values()
    ):
        instance.status = WorkflowInstanceStatus.BLOCKED
    elif any(
        node.status == WorkflowNodeStatus.WAITING_CONFIRMATION
        for node in nodes.values()
    ):
        instance.status = WorkflowInstanceStatus.WAITING
    else:
        instance.status = WorkflowInstanceStatus.RUNNING
    instance.updated_at = utcnow()
    session.add(instance)


def instance_version_exit_nodes(session: Session, version_id: uuid.UUID) -> list[str]:
    version = session.get(WorkflowTemplateVersion, version_id)
    if version is None:
        return []
    return [str(item) for item in version.manifest.get("exit_nodes", [])]


def _reconcile_runtime_executions(
    session: Session,
    instance: WorkflowInstance,
    definitions: list[WorkflowNodeDefinition],
    edges: list[WorkflowEdgeDefinition],
) -> bool:
    executions = session.exec(
        select(WorkflowNodeExecution).where(
            col(WorkflowNodeExecution.node_instance_id).in_(
                select(WorkflowNodeInstance.id).where(
                    WorkflowNodeInstance.workflow_instance_id == instance.id
                )
            ),
            col(WorkflowNodeExecution.runtime_job_id).is_not(None),
            col(WorkflowNodeExecution.status).in_(
                [WorkflowExecutionStatus.QUEUED, WorkflowExecutionStatus.RUNNING]
            ),
        )
    ).all()
    definition_by_id = {item.id: item for item in definitions}
    changed = False
    for execution in executions:
        job = session.get(RuntimeJob, execution.runtime_job_id)
        node = session.get(WorkflowNodeInstance, execution.node_instance_id)
        if job is None or node is None:
            execution.status = WorkflowExecutionStatus.FAILED
            execution.error = {"code": "runtime_execution_record_missing"}
            if node is not None:
                node.status = WorkflowNodeStatus.FAILED
                session.add(node)
            session.add(execution)
            changed = True
            continue
        if job.status in {
            RuntimeJobStatus.QUEUED,
            RuntimeJobStatus.DISPATCHED,
            RuntimeJobStatus.RUNNING,
        }:
            if (
                execution.status != WorkflowExecutionStatus.RUNNING
                and job.status != RuntimeJobStatus.QUEUED
            ):
                execution.status = WorkflowExecutionStatus.RUNNING
                session.add(execution)
                changed = True
            continue
        definition = definition_by_id[node.node_definition_id]
        execution.completed_at = job.completed_at or utcnow()
        if job.status in {
            RuntimeJobStatus.FAILED,
            RuntimeJobStatus.NEEDS_MANUAL_RESOLUTION,
        }:
            execution.status = (
                WorkflowExecutionStatus.NEEDS_MANUAL_RESOLUTION
                if job.status == RuntimeJobStatus.NEEDS_MANUAL_RESOLUTION
                else WorkflowExecutionStatus.FAILED
            )
            execution.error = job.error or {"code": "runtime_job_failed"}
            node.status = (
                WorkflowNodeStatus.NEEDS_MANUAL_RESOLUTION
                if job.status == RuntimeJobStatus.NEEDS_MANUAL_RESOLUTION
                else WorkflowNodeStatus.FAILED
            )
            instance.status = (
                WorkflowInstanceStatus.BLOCKED
                if job.status == RuntimeJobStatus.NEEDS_MANUAL_RESOLUTION
                else WorkflowInstanceStatus.FAILED
            )
            append_event(
                session,
                instance.id,
                "node_failed",
                {
                    "node_key": node.node_key,
                    "phase": execution.phase,
                    "error": execution.error,
                },
            )
            session.add_all([execution, node, instance])
            changed = True
            continue
        result = job.result or {}
        if execution.phase in {"entry_gate", "exit_gate"}:
            passed = bool(result.get("passed"))
            details = (
                dict(result["details"])
                if isinstance(result.get("details"), dict)
                else {"code": "validator_result_invalid"}
            )
            key = (
                definition.entry_validator_key
                if execution.phase == "entry_gate"
                else definition.exit_validator_key
            )
            assert key is not None
            _record_gate_result(
                session,
                instance,
                node,
                GateType.ENTRY if execution.phase == "entry_gate" else GateType.EXIT,
                key,
                str(execution.pending_payload.get("gate_input_digest", "")),
                execution.input_revision,
                passed,
                details,
            )
            execution.status = (
                WorkflowExecutionStatus.COMPLETED
                if passed
                else WorkflowExecutionStatus.FAILED
            )
            if not passed:
                execution.error = {"code": f"{execution.phase}_failed"}
                node.status = WorkflowNodeStatus.BLOCKED
                instance.status = WorkflowInstanceStatus.BLOCKED
            elif execution.phase == "entry_gate":
                input_snapshot = build_node_input(session, instance, node, edges)
                previous = _current_revision(session, node)
                _continue_after_entry_gate(
                    session,
                    instance,
                    node,
                    definition,
                    input_snapshot,
                    previous,
                )
            else:
                revision = session.get(
                    WorkflowNodeRevision,
                    uuid.UUID(str(execution.pending_payload["revision_id"])),
                )
                if revision is None:
                    execution.status = WorkflowExecutionStatus.FAILED
                    execution.error = {"code": "exit_gate_revision_missing"}
                    node.status = WorkflowNodeStatus.FAILED
                    instance.status = WorkflowInstanceStatus.FAILED
                else:
                    _complete_after_exit_gate(
                        session, instance, node, definition, revision
                    )
                    if execution.pending_payload.get("propagate_on_success"):
                        propagate_update(session, instance, node.node_key)
                    if execution.pending_payload.get("submit_event"):
                        append_event(
                            session,
                            instance.id,
                            "node_submitted",
                            {
                                "node_key": node.node_key,
                                "revision": revision.revision,
                            },
                        )
        elif execution.phase == "run":
            output = result.get("output")
            if not isinstance(output, dict):
                execution.status = WorkflowExecutionStatus.FAILED
                execution.error = {"code": "runtime_handler_result_invalid"}
                node.status = WorkflowNodeStatus.FAILED
                instance.status = WorkflowInstanceStatus.FAILED
            else:
                output = dict(output)
                proof = output.pop("_external_state_proof", None)
                schema_errors = validate_json_value(output, definition.output_schema)
                if schema_errors:
                    execution.status = WorkflowExecutionStatus.FAILED
                    execution.error = {
                        "code": "node_output_schema_invalid",
                        "errors": schema_errors,
                    }
                    node.status = WorkflowNodeStatus.FAILED
                    instance.status = WorkflowInstanceStatus.FAILED
                else:
                    if definition.side_effecting and not (
                        isinstance(proof, dict) and proof
                    ):
                        execution.status = (
                            WorkflowExecutionStatus.NEEDS_MANUAL_RESOLUTION
                        )
                        execution.error = {"code": "external_state_proof_missing"}
                        node.status = WorkflowNodeStatus.NEEDS_MANUAL_RESOLUTION
                        instance.status = WorkflowInstanceStatus.BLOCKED
                    else:
                        execution.external_state_proof = proof
                        execution.status = WorkflowExecutionStatus.COMPLETED
                        input_snapshot = build_node_input(
                            session, instance, node, edges
                        )
                        previous = _current_revision(session, node)
                        revision = append_revision(
                            session,
                            node,
                            input_snapshot,
                            output,
                            "code node run(context) completed in Runtime",
                            None,
                        )
                        _start_exit_gate(
                            session,
                            instance,
                            node,
                            definition,
                            input_snapshot,
                            previous,
                            revision,
                        )
        session.add_all([execution, node, instance])
        changed = True
    return changed


def reconcile_instance(session: Session, instance: WorkflowInstance) -> bool:
    executions = session.exec(
        select(WorkflowNodeExecution).where(
            col(WorkflowNodeExecution.node_instance_id).in_(
                select(WorkflowNodeInstance.id).where(
                    WorkflowNodeInstance.workflow_instance_id == instance.id
                )
            ),
            WorkflowNodeExecution.status == WorkflowExecutionStatus.RUNNING,
            col(WorkflowNodeExecution.agent_task_id).is_not(None),
        )
    ).all()
    changed = False
    definitions, edges = _definitions(session, instance.template_version_id)
    changed = (
        _reconcile_runtime_executions(session, instance, definitions, edges) or changed
    )
    definition_by_id = {item.id: item for item in definitions}
    for execution in executions:
        task = session.get(AgentTask, execution.agent_task_id)
        node = session.get(WorkflowNodeInstance, execution.node_instance_id)
        if task is None or node is None:
            execution.status = WorkflowExecutionStatus.FAILED
            execution.error = {"code": "agent_execution_record_missing"}
            if node:
                node.status = WorkflowNodeStatus.FAILED
            changed = True
            continue
        definition = definition_by_id[node.node_definition_id]
        if task.status == TaskStatus.SUCCEEDED:
            if execution.input_revision != node.expected_revision + 1:
                execution.status = WorkflowExecutionStatus.SUPERSEDED
                execution.completed_at = utcnow()
                node.status = WorkflowNodeStatus.UPDATE_REQUIRED
                changed = True
                continue
            output = dict(task.final_result or {})
            proof = output.pop("_external_state_proof", None)
            schema_errors = validate_json_value(output, definition.output_schema)
            if schema_errors:
                execution.status = WorkflowExecutionStatus.FAILED
                execution.error = {
                    "code": "agent_output_schema_invalid",
                    "errors": schema_errors,
                }
                node.status = WorkflowNodeStatus.FAILED
                changed = True
                continue
            if definition.side_effecting and not (isinstance(proof, dict) and proof):
                execution.status = WorkflowExecutionStatus.NEEDS_MANUAL_RESOLUTION
                execution.error = {"code": "external_state_proof_missing"}
                execution.completed_at = utcnow()
                node.status = WorkflowNodeStatus.NEEDS_MANUAL_RESOLUTION
                instance.status = WorkflowInstanceStatus.BLOCKED
                session.add_all([execution, node, instance])
                changed = True
                continue
            input_snapshot = build_node_input(session, instance, node, edges)
            previous = _current_revision(session, node)
            revision = append_revision(
                session,
                node,
                input_snapshot,
                output,
                "Agent node produced a formal result",
                None,
            )
            execution.status = WorkflowExecutionStatus.COMPLETED
            execution.external_state_proof = proof
            execution.completed_at = utcnow()
            _start_exit_gate(
                session,
                instance,
                node,
                definition,
                input_snapshot,
                previous,
                revision,
            )
            changed = True
        elif task.status in {
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        }:
            execution.status = WorkflowExecutionStatus.FAILED
            execution.error = task.final_result or {"code": task.status.value}
            execution.completed_at = utcnow()
            node.status = WorkflowNodeStatus.FAILED
            changed = True
        session.add_all([execution, node])
    if changed:
        advance_instance(session, instance)
        session.flush()
    return changed


def create_instance_nodes(
    session: Session,
    instance: WorkflowInstance,
    runtime_resolution: dict[str, Any],
) -> None:
    definitions, edges = _definitions(session, instance.template_version_id)
    incoming_keys = {edge.target_node_key for edge in edges}
    for definition in definitions:
        raw_runtime_id = runtime_resolution[definition.node_key].get("runtime_id")
        node = WorkflowNodeInstance(
            workflow_instance_id=instance.id,
            node_definition_id=definition.id,
            node_key=definition.node_key,
            status=(
                WorkflowNodeStatus.READY
                if definition.node_key not in incoming_keys
                else WorkflowNodeStatus.INACTIVE
            ),
            resolved_runtime_id=(
                uuid.UUID(str(raw_runtime_id)) if raw_runtime_id else None
            ),
        )
        session.add(node)
    session.flush()
    instance.status = WorkflowInstanceStatus.RUNNING
    append_event(session, instance.id, "task_created", {"title": instance.title})
    advance_instance(session, instance)


def propagate_update(
    session: Session, instance: WorkflowInstance, source_node_key: str
) -> None:
    _, edges = _definitions(session, instance.template_version_id)
    nodes = _node_map(session, instance.id)
    queue = [source_node_key]
    visited: set[str] = set()
    while queue:
        source = queue.pop(0)
        for edge in _outgoing_edges(edges, source):
            target_key = edge.target_node_key
            if target_key in visited:
                continue
            visited.add(target_key)
            target = nodes[target_key]
            if target.status not in {
                WorkflowNodeStatus.INACTIVE,
                WorkflowNodeStatus.READY,
            }:
                target.status = WorkflowNodeStatus.UPDATE_REQUIRED
                target.updated_at = utcnow()
                session.add(target)
                append_event(
                    session,
                    instance.id,
                    "node_update_required",
                    {"node_key": target_key, "source_node_key": source_node_key},
                )
            queue.append(target_key)
    instance.status = WorkflowInstanceStatus.RUNNING
    instance.completed_at = None
    session.add(instance)


def submit_node(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    output: dict[str, Any],
    reason: str,
    user_id: uuid.UUID,
) -> WorkflowNodeRevision:
    definitions, edges = _definitions(session, instance.template_version_id)
    del definitions
    input_snapshot = build_node_input(session, instance, node, edges)
    schema_errors = validate_json_value(output, definition.output_schema)
    if schema_errors:
        raise HTTPException(
            422, {"code": "output_schema_invalid", "errors": schema_errors}
        )
    previous = _current_revision(session, node)
    revision = append_revision(session, node, input_snapshot, output, reason, user_id)
    session.add(
        WorkflowConfirmation(
            node_instance_id=node.id,
            revision=revision.revision,
            mode=definition.confirmation_mode,
            decision=ConfirmationDecision.SUBMIT,
            user_id=user_id,
            reason=reason,
        )
    )
    _start_exit_gate(
        session,
        instance,
        node,
        definition,
        input_snapshot,
        previous,
        revision,
    )
    if definition.exit_validator_key:
        pending = session.exec(
            select(WorkflowNodeExecution)
            .where(
                WorkflowNodeExecution.node_instance_id == node.id,
                WorkflowNodeExecution.phase == "exit_gate",
                WorkflowNodeExecution.status == WorkflowExecutionStatus.QUEUED,
            )
            .order_by(col(WorkflowNodeExecution.attempt).desc())
        ).first()
        if pending is not None:
            pending.pending_payload = {
                **pending.pending_payload,
                "propagate_on_success": previous is not None,
                "submit_event": True,
            }
            session.add(pending)
    else:
        if previous is not None:
            propagate_update(session, instance, node.node_key)
        append_event(
            session,
            instance.id,
            "node_submitted",
            {"node_key": node.node_key, "revision": revision.revision},
        )
        advance_instance(session, instance)
    return revision
