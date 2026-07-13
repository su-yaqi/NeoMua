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
    SpecStandard,
    SpecStandardVersion,
)
from app.runtime.models import AgentTask, RuntimeProfile
from app.runtime.policy import TaskStatus
from app.workflow_management.models import (
    ConfirmationDecision,
    ConfirmationMode,
    GateType,
    NamespaceWorkflowEnablement,
    WorkflowConfirmation,
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
    WorkflowTemplateVersion,
    WorkflowVersionStatus,
)
from app.workflow_management.sdk import (
    EDGE_CONDITIONS,
    NODE_HANDLERS,
    VALIDATORS,
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
        }
        for item in repositories
    ]
    spec_refs: list[dict[str, Any]] = []
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
        if binding is None or version is None or standard is None:
            raise HTTPException(
                409,
                {"code": "spec_binding_unavailable", "location_id": str(location.id)},
            )
        repository = next(
            item for item in repositories if item.id == location.repository_id
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
    snapshot = {"repository_refs": repository_refs, "spec_refs": spec_refs}
    return {**snapshot, "content_digest": canonical_digest(snapshot)}


def preflight(
    session: Session,
    namespace_id: uuid.UUID,
    project: Project,
    version: WorkflowTemplateVersion,
    task_runtime_id: uuid.UUID | None,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if project.status == ProjectStatus.ARCHIVED:
        errors.append({"code": "project_archived", "message": "Project is archived"})
    if version.status != WorkflowVersionStatus.ACTIVE:
        errors.append(
            {"code": "template_version_unavailable", "message": version.status.value}
        )
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
    context_snapshot: dict[str, Any] | None = None
    try:
        context_snapshot = project_context_snapshot(session, project)
    except HTTPException as exc:
        errors.append({"code": "project_context_invalid", "message": exc.detail})
    definitions, _ = _definitions(session, version.id)
    runtime_resolution: dict[str, Any] = {}
    for definition in definitions:
        explicit_runtime = definition.runtime_policy.get("runtime_id")
        runtime_id = (
            uuid.UUID(str(explicit_runtime))
            if explicit_runtime
            else task_runtime_id or project.default_runtime_id
        )
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
        if definition.node_type == WorkflowNodeType.AGENT:
            binding = session.exec(
                select(RuntimeAgentRelease).where(
                    RuntimeAgentRelease.runtime_profile_id == runtime.id,
                    RuntimeAgentRelease.current_release_id
                    == definition.agent_release_id,
                )
            ).first()
            release = (
                session.get(AgentRelease, definition.agent_release_id)
                if definition.agent_release_id
                else None
            )
            if (
                binding is None
                or release is None
                or binding.applied_digest != release.resolved_spec_digest
                or binding.materialization_digest != release.resolved_spec_digest
            ):
                errors.append(
                    {
                        "code": "agent_release_not_active",
                        "node_key": definition.node_key,
                    }
                )
            else:
                runtime_resolution[definition.node_key] = {
                    "runtime_id": str(runtime.id),
                    "agent_release_id": str(release.id),
                    "resolved_spec_digest": release.resolved_spec_digest,
                }
                continue
        runtime_resolution[definition.node_key] = {"runtime_id": str(runtime.id)}
    return {
        "passed": not errors,
        "errors": errors,
        "runtime_resolution": runtime_resolution,
        "project_context_snapshot": context_snapshot,
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
        project_id=str(instance.project_id),
        input=input_snapshot,
        previous_output=previous.output if previous else None,
        change_summary={
            "previous_input_digest": previous.input_digest if previous else None,
            "new_input_digest": canonical_digest(input_snapshot),
        },
        idempotency_key=idempotency_key,
    )


def run_gate(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    gate_type: GateType,
    input_snapshot: dict[str, Any],
    previous: WorkflowNodeRevision | None,
    revision: int,
) -> bool:
    key = (
        definition.entry_validator_key
        if gate_type == GateType.ENTRY
        else definition.exit_validator_key
    )
    if not key:
        return True
    validator = VALIDATORS.get(key)
    if validator is None:
        node.status = WorkflowNodeStatus.FAILED
        session.add(
            WorkflowGateResult(
                node_instance_id=node.id,
                revision=revision,
                gate_type=gate_type,
                validator_key=key,
                validator_version=instance.package_digest,
                input_digest=canonical_digest(input_snapshot),
                passed=False,
                details={"code": "validator_not_registered"},
            )
        )
        return False
    try:
        passed, details = validator(
            _run_context(
                instance,
                node,
                input_snapshot,
                previous,
                f"gate:{instance.id}:{node.node_key}:{revision}:{gate_type.value}",
            )
        )
    except Exception as exc:
        passed = False
        details = {"code": "validator_exception", "message": str(exc)}
        node.status = WorkflowNodeStatus.FAILED
    session.add(
        WorkflowGateResult(
            node_instance_id=node.id,
            revision=revision,
            gate_type=gate_type,
            validator_key=key,
            validator_version=instance.package_digest,
            input_digest=canonical_digest(input_snapshot),
            passed=passed,
            details=details,
        )
    )
    if not passed and node.status != WorkflowNodeStatus.FAILED:
        node.status = WorkflowNodeStatus.BLOCKED
    return passed


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
        status=WorkflowExecutionStatus.RUNNING,
        idempotency_key=f"workflow:{node.workflow_instance_id}:node:{node.node_key}:revision:{input_revision}:attempt:{attempt}",
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
    handler = NODE_HANDLERS.get(definition.handler_key or "")
    if handler is None:
        node.status = WorkflowNodeStatus.FAILED
        append_event(
            session,
            instance.id,
            "node_failed",
            {"node_key": node.node_key, "code": "handler_not_registered"},
        )
        return
    execution = _new_execution(session, node, node.expected_revision + 1)
    try:
        output = handler(
            _run_context(
                instance,
                node,
                input_snapshot,
                previous,
                execution.idempotency_key,
            )
        )
        schema_errors = validate_json_value(output, definition.output_schema)
        if schema_errors:
            raise ValueError("; ".join(schema_errors))
        proof = output.pop("_external_state_proof", None)
        if definition.side_effecting and not proof:
            execution.status = WorkflowExecutionStatus.NEEDS_MANUAL_RESOLUTION
            execution.error = {"code": "external_state_proof_missing"}
            node.status = WorkflowNodeStatus.NEEDS_MANUAL_RESOLUTION
            instance.status = WorkflowInstanceStatus.BLOCKED
            return
        execution.external_state_proof = proof
        revision = append_revision(
            session,
            node,
            input_snapshot,
            output,
            "code node run(context) completed",
            None,
        )
        if not run_gate(
            session,
            instance,
            node,
            definition,
            GateType.EXIT,
            {"input": input_snapshot, "output": output},
            previous,
            revision.revision,
        ):
            execution.status = WorkflowExecutionStatus.FAILED
            execution.error = {"code": "exit_gate_failed"}
            return
        execution.status = WorkflowExecutionStatus.COMPLETED
        execution.completed_at = utcnow()
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
    except Exception as exc:
        execution.status = WorkflowExecutionStatus.FAILED
        execution.error = {"code": "node_execution_failed", "message": str(exc)}
        execution.completed_at = utcnow()
        node.status = WorkflowNodeStatus.FAILED
        instance.status = WorkflowInstanceStatus.FAILED
        append_event(
            session,
            instance.id,
            "node_failed",
            {"node_key": node.node_key, "message": str(exc)},
        )
    finally:
        session.add_all([execution, node, instance])


def _execute_agent_node(
    session: Session,
    instance: WorkflowInstance,
    node: WorkflowNodeInstance,
    definition: WorkflowNodeDefinition,
    input_snapshot: dict[str, Any],
    previous: WorkflowNodeRevision | None,
) -> None:
    release = (
        session.get(AgentRelease, definition.agent_release_id)
        if definition.agent_release_id
        else None
    )
    binding = session.exec(
        select(RuntimeAgentRelease).where(
            RuntimeAgentRelease.runtime_profile_id == node.resolved_runtime_id,
            RuntimeAgentRelease.current_release_id == definition.agent_release_id,
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
    ) + str(input_snapshot)
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
    if not run_gate(
        session,
        instance,
        node,
        definition,
        GateType.ENTRY,
        input_snapshot,
        previous,
        node.expected_revision + 1,
    ):
        instance.status = (
            WorkflowInstanceStatus.FAILED
            if node.status == WorkflowNodeStatus.FAILED
            else WorkflowInstanceStatus.BLOCKED
        )
        session.add_all([node, instance])
        return
    if definition.confirmation_mode == ConfirmationMode.PROCESS:
        node.status = WorkflowNodeStatus.WAITING_CONFIRMATION
        instance.status = WorkflowInstanceStatus.WAITING
    elif definition.node_type == WorkflowNodeType.CODE:
        node.status = WorkflowNodeStatus.RUNNING
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


def reconcile_instance(session: Session, instance: WorkflowInstance) -> None:
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
            output = task.final_result or {}
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
            if run_gate(
                session,
                instance,
                node,
                definition,
                GateType.EXIT,
                {"input": input_snapshot, "output": output},
                previous,
                revision.revision,
            ):
                node.status = (
                    WorkflowNodeStatus.WAITING_CONFIRMATION
                    if definition.confirmation_mode == ConfirmationMode.RESULT
                    else WorkflowNodeStatus.COMPLETED
                )
                execution.status = WorkflowExecutionStatus.COMPLETED
            else:
                execution.status = WorkflowExecutionStatus.FAILED
                execution.error = {"code": "exit_gate_failed"}
            execution.completed_at = utcnow()
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
        session.commit()


def create_instance_nodes(
    session: Session,
    instance: WorkflowInstance,
    runtime_resolution: dict[str, Any],
) -> None:
    definitions, edges = _definitions(session, instance.template_version_id)
    incoming_keys = {edge.target_node_key for edge in edges}
    for definition in definitions:
        node = WorkflowNodeInstance(
            workflow_instance_id=instance.id,
            node_definition_id=definition.id,
            node_key=definition.node_key,
            status=(
                WorkflowNodeStatus.READY
                if definition.node_key not in incoming_keys
                else WorkflowNodeStatus.INACTIVE
            ),
            resolved_runtime_id=uuid.UUID(
                runtime_resolution[definition.node_key]["runtime_id"]
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
    if not run_gate(
        session,
        instance,
        node,
        definition,
        GateType.EXIT,
        {"input": input_snapshot, "output": output},
        previous,
        revision.revision,
    ):
        session.add(node)
        return revision
    node.status = WorkflowNodeStatus.COMPLETED
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
