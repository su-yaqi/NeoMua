import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    require_namespace_admin,
    require_namespace_member,
)
from app.conversation_management.models import (
    Conversation,
    ConversationAgent,
    ConversationMessage,
    MessageAuthorType,
    MessageStatus,
    MessageTargetType,
)
from app.conversation_management.service import create_agent_task, next_message_sequence
from app.project_management.service import can_manage_namespace, require_project_member
from app.runtime.security import require_internal_runtime
from app.workflow_management.bundled import load_bundled_workflow_code
from app.workflow_management.engine import (
    advance_instance,
    append_event,
    append_revision,
    build_node_input,
    create_instance_nodes,
    get_instance,
    get_node,
    preflight,
    reconcile_instance,
    require_expected_revision,
    require_mutable_instance,
    submit_node,
    utcnow,
    validate_json_value,
)
from app.workflow_management.models import (
    ConfirmationDecision,
    ConfirmationMode,
    NamespaceWorkflowEnablement,
    WorkflowApplication,
    WorkflowConfirmation,
    WorkflowEdgeDefinition,
    WorkflowEvent,
    WorkflowExecutionStatus,
    WorkflowInstance,
    WorkflowInstanceStatus,
    WorkflowNodeDefinition,
    WorkflowNodeExecution,
    WorkflowNodeInstance,
    WorkflowNodeRevision,
    WorkflowNodeStatus,
    WorkflowNodeType,
    WorkflowTemplate,
    WorkflowTemplateVersion,
    WorkflowVersionStatus,
)
from app.workflow_management.package import (
    PackageValidationError,
    package_digest,
    validate_package,
)
from app.workflow_management.schemas import (
    EnablementUpdate,
    ExternalStateResolution,
    NodeConfirmation,
    NodeMessage,
    NodeMutation,
    NodeRetry,
    NodeSkip,
    RegistrySync,
    WorkflowInstanceCreate,
    WorkflowPreflight,
)

router = APIRouter(tags=["workflows"])
internal_router = APIRouter(
    prefix="/internal/workflow-registry",
    tags=["workflow-registry"],
    dependencies=[Depends(require_internal_runtime)],
)

load_bundled_workflow_code()


def _template_visible(
    session: SessionDep,
    template_id: uuid.UUID,
    namespace_id: uuid.UUID,
) -> WorkflowTemplate:
    template = session.get(WorkflowTemplate, template_id)
    if template is None or (
        template.namespace_id is not None and template.namespace_id != namespace_id
    ):
        raise HTTPException(404, "Workflow Template not found")
    return template


def _version_visible(
    session: SessionDep,
    version_id: uuid.UUID,
    namespace_id: uuid.UUID,
) -> tuple[WorkflowTemplateVersion, WorkflowTemplate]:
    version = session.get(WorkflowTemplateVersion, version_id)
    if version is None:
        raise HTTPException(404, "Workflow Template Version not found")
    return version, _template_visible(session, version.template_id, namespace_id)


def _instance_public(session: SessionDep, instance: WorkflowInstance) -> dict[str, Any]:
    version = session.get(WorkflowTemplateVersion, instance.template_version_id)
    template = session.get(WorkflowTemplate, version.template_id) if version else None
    nodes = session.exec(
        select(WorkflowNodeInstance)
        .where(WorkflowNodeInstance.workflow_instance_id == instance.id)
        .order_by(col(WorkflowNodeInstance.created_at))
    ).all()
    return {
        "id": instance.id,
        "namespace_id": instance.namespace_id,
        "project_id": instance.project_id,
        "template_version_id": instance.template_version_id,
        "workflow_slug": template.slug if template else None,
        "package_digest": instance.package_digest,
        "title": instance.title,
        "status": instance.status,
        "input": instance.input,
        "default_runtime_id": instance.default_runtime_id,
        "runtime_resolution": instance.runtime_resolution,
        "project_context_snapshot": instance.project_context_snapshot,
        "nodes": nodes,
        "created_by": instance.created_by,
        "created_at": instance.created_at,
        "updated_at": instance.updated_at,
        "completed_at": instance.completed_at,
        "cancelled_at": instance.cancelled_at,
    }


@internal_router.post("/sync")
def sync_registry(
    body: RegistrySync,
    session: SessionDep,
) -> dict[str, Any]:
    try:
        manifest = validate_package(body.manifest)
    except PackageValidationError as exc:
        raise HTTPException(422, {"errors": exc.errors}) from exc
    computed_digest = package_digest(manifest)
    if computed_digest != body.package_digest:
        raise HTTPException(
            409,
            {
                "code": "package_digest_mismatch",
                "expected": computed_digest,
                "received": body.package_digest,
            },
        )
    namespace_id = uuid.UUID(manifest.namespace_id) if manifest.namespace_id else None
    template = session.exec(
        select(WorkflowTemplate).where(
            WorkflowTemplate.scope_type == manifest.scope_type,
            WorkflowTemplate.namespace_id == namespace_id,
            WorkflowTemplate.slug == manifest.slug,
        )
    ).first()
    if template is None:
        template = WorkflowTemplate(
            scope_type=manifest.scope_type,
            namespace_id=namespace_id,
            slug=manifest.slug,
            name=manifest.name,
            description=manifest.description,
        )
        session.add(template)
        session.flush()
    elif template.name != manifest.name or template.description != manifest.description:
        # Stable template identity is immutable; copy changes belong in version metadata.
        raise HTTPException(409, "Workflow Template identity metadata is immutable")
    existing = session.exec(
        select(WorkflowTemplateVersion).where(
            WorkflowTemplateVersion.template_id == template.id,
            WorkflowTemplateVersion.version == manifest.version,
        )
    ).first()
    if existing:
        if existing.package_digest != computed_digest:
            raise HTTPException(409, "Workflow Template Version is immutable")
        return {
            "template_id": template.id,
            "template_version_id": existing.id,
            "created": False,
        }
    version = WorkflowTemplateVersion(
        template_id=template.id,
        version=manifest.version,
        manifest=manifest.model_dump(mode="json", exclude_none=True),
        package_digest=computed_digest,
        sdk_version=manifest.sdk_version,
        build_metadata=body.build_metadata,
    )
    session.add(version)
    session.flush()
    for position, node in enumerate(manifest.nodes):
        session.add(
            WorkflowNodeDefinition(
                template_version_id=version.id,
                node_key=node.key,
                name=node.name,
                node_type=node.type,
                confirmation_mode=node.confirmation_mode,
                input_schema=node.input_schema,
                output_schema=node.output_schema,
                runtime_policy=node.runtime_policy,
                agent_release_id=(
                    uuid.UUID(node.agent_release_id) if node.agent_release_id else None
                ),
                handler_key=node.handler_key,
                entry_validator_key=node.validate_in,
                exit_validator_key=node.validate_out,
                skippable=node.skippable,
                skip_output=node.skip_output,
                side_effecting=node.side_effecting,
                join_policy=node.join_policy,
                position=position,
            )
        )
    for edge in manifest.edges:
        session.add(
            WorkflowEdgeDefinition(
                template_version_id=version.id,
                source_node_key=edge.source,
                target_node_key=edge.target,
                condition_key=edge.condition_key,
                condition_config=edge.condition_config,
            )
        )
    application = session.exec(
        select(WorkflowApplication).where(
            WorkflowApplication.template_id == template.id
        )
    ).first()
    if application is None:
        application = WorkflowApplication(
            template_id=template.id,
            component_key=manifest.application.component_key,
            route_slug=manifest.application.route_slug,
            build_digest=manifest.application.build_digest,
            shell_version=manifest.application.shell_version,
        )
    else:
        application.component_key = manifest.application.component_key
        application.route_slug = manifest.application.route_slug
        application.build_digest = manifest.application.build_digest
        application.shell_version = manifest.application.shell_version
    session.add(application)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "Workflow Package registry conflict") from exc
    return {
        "template_id": template.id,
        "template_version_id": version.id,
        "created": True,
    }


@router.get("/workflow-templates")
def list_workflow_templates(
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    templates = session.exec(
        select(WorkflowTemplate).where(
            or_(
                col(WorkflowTemplate.namespace_id) == namespace_id,
                col(WorkflowTemplate.namespace_id).is_(None),
            )
        )
    ).all()
    data: list[dict[str, Any]] = []
    for template in templates:
        versions = session.exec(
            select(WorkflowTemplateVersion).where(
                WorkflowTemplateVersion.template_id == template.id
            )
        ).all()
        application = session.exec(
            select(WorkflowApplication).where(
                WorkflowApplication.template_id == template.id
            )
        ).first()
        data.append(
            {
                "id": template.id,
                "slug": template.slug,
                "name": template.name,
                "description": template.description,
                "scope_type": template.scope_type,
                "application": application,
                "versions": [
                    {
                        "version": version,
                        "enablement": session.exec(
                            select(NamespaceWorkflowEnablement).where(
                                NamespaceWorkflowEnablement.namespace_id
                                == namespace_id,
                                NamespaceWorkflowEnablement.template_version_id
                                == version.id,
                            )
                        ).first()
                        or {"enabled": False, "is_default": False},
                    }
                    for version in versions
                ],
            }
        )
    return {"data": data, "count": len(data)}


@router.get("/workflow-templates/{template_id}/versions/{version_id}")
def read_workflow_version(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    template = _template_visible(session, template_id, namespace_id)
    version = session.get(WorkflowTemplateVersion, version_id)
    if version is None or version.template_id != template.id:
        raise HTTPException(404, "Workflow Template Version not found")
    nodes = session.exec(
        select(WorkflowNodeDefinition).where(
            WorkflowNodeDefinition.template_version_id == version.id
        )
    ).all()
    edges = session.exec(
        select(WorkflowEdgeDefinition).where(
            WorkflowEdgeDefinition.template_version_id == version.id
        )
    ).all()
    application = session.exec(
        select(WorkflowApplication).where(
            WorkflowApplication.template_id == template.id
        )
    ).first()
    return {
        "template": template,
        "version": version,
        "nodes": nodes,
        "edges": edges,
        "application": application,
    }


@router.get("/workflow-templates/{template_id}/enablement")
def read_enablement(
    template_id: uuid.UUID,
    session: SessionDep,
    _: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    _template_visible(session, template_id, namespace_id)
    rows = session.exec(
        select(NamespaceWorkflowEnablement, WorkflowTemplateVersion)
        .join(
            WorkflowTemplateVersion,
            col(WorkflowTemplateVersion.id)
            == col(NamespaceWorkflowEnablement.template_version_id),
        )
        .where(
            NamespaceWorkflowEnablement.namespace_id == namespace_id,
            WorkflowTemplateVersion.template_id == template_id,
        )
    ).all()
    return {
        "data": [{"enablement": item, "version": version} for item, version in rows],
        "count": len(rows),
    }


@router.put("/workflow-templates/{template_id}/enablement")
def update_enablement(
    template_id: uuid.UUID,
    body: EnablementUpdate,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> NamespaceWorkflowEnablement:
    template = _template_visible(session, template_id, namespace_id)
    version = session.get(WorkflowTemplateVersion, body.template_version_id)
    if version is None or version.template_id != template.id:
        raise HTTPException(404, "Workflow Template Version not found")
    if version.status == WorkflowVersionStatus.BLOCKED and body.enabled:
        raise HTTPException(409, "Blocked Workflow versions cannot be enabled")
    if body.is_default and not body.enabled:
        raise HTTPException(422, "Default Workflow version must be enabled")
    row = session.exec(
        select(NamespaceWorkflowEnablement).where(
            NamespaceWorkflowEnablement.namespace_id == namespace_id,
            NamespaceWorkflowEnablement.template_version_id == version.id,
        )
    ).first()
    if row is None:
        row = NamespaceWorkflowEnablement(
            namespace_id=namespace_id,
            template_version_id=version.id,
            updated_by=current_user.id,
        )
    row.enabled = body.enabled
    row.is_default = body.is_default
    row.updated_by = current_user.id
    row.updated_at = utcnow()
    if body.is_default:
        other_rows = session.exec(
            select(NamespaceWorkflowEnablement)
            .join(
                WorkflowTemplateVersion,
                col(WorkflowTemplateVersion.id)
                == col(NamespaceWorkflowEnablement.template_version_id),
            )
            .where(
                NamespaceWorkflowEnablement.namespace_id == namespace_id,
                WorkflowTemplateVersion.template_id == template.id,
                NamespaceWorkflowEnablement.template_version_id != version.id,
            )
        ).all()
        for other in other_rows:
            other.is_default = False
            session.add(other)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.post("/workflow-templates/{template_id}/preflight")
def preflight_workflow(
    template_id: uuid.UUID,
    body: WorkflowPreflight,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    template = _template_visible(session, template_id, namespace_id)
    version = session.get(WorkflowTemplateVersion, body.template_version_id)
    if version is None or version.template_id != template.id:
        raise HTTPException(404, "Workflow Template Version not found")
    project = require_project_member(
        session, body.project_id, namespace_id, current_user
    )
    return preflight(session, namespace_id, project, version, body.runtime_id)


@router.get("/projects/{project_id}/workflow-instances")
def list_workflow_instances(
    project_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    require_project_member(session, project_id, namespace_id, current_user)
    rows = session.exec(
        select(WorkflowInstance)
        .where(WorkflowInstance.project_id == project_id)
        .order_by(col(WorkflowInstance.updated_at).desc())
    ).all()
    for row in rows:
        reconcile_instance(session, row)
    return {
        "data": [_instance_public(session, row) for row in rows],
        "count": len(rows),
    }


@router.post("/projects/{project_id}/workflow-instances", status_code=201)
def create_workflow_instance(
    project_id: uuid.UUID,
    body: WorkflowInstanceCreate,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    project = require_project_member(session, project_id, namespace_id, current_user)
    version, _ = _version_visible(session, body.template_version_id, namespace_id)
    existing = session.exec(
        select(WorkflowInstance).where(
            WorkflowInstance.namespace_id == namespace_id,
            WorkflowInstance.idempotency_key == idempotency_key,
        )
    ).first()
    if existing:
        if (
            existing.project_id != project_id
            or existing.template_version_id != version.id
            or existing.input != body.input
            or existing.title != body.title
            or existing.default_runtime_id
            != (body.runtime_id or project.default_runtime_id)
        ):
            raise HTTPException(409, "Idempotency-Key was used for a different task")
        return _instance_public(session, existing)
    input_errors = validate_json_value(
        body.input, version.manifest.get("input_schema", {})
    )
    if input_errors:
        raise HTTPException(
            422, {"code": "workflow_input_invalid", "errors": input_errors}
        )
    result = preflight(session, namespace_id, project, version, body.runtime_id)
    if not result["passed"]:
        raise HTTPException(
            409, {"code": "workflow_preflight_failed", "errors": result["errors"]}
        )
    instance = WorkflowInstance(
        namespace_id=namespace_id,
        project_id=project.id,
        template_version_id=version.id,
        package_digest=version.package_digest,
        title=body.title,
        input=body.input,
        default_runtime_id=body.runtime_id or project.default_runtime_id,
        runtime_resolution=result["runtime_resolution"],
        project_context_snapshot=result["project_context_snapshot"],
        idempotency_key=idempotency_key,
        created_by=current_user.id,
    )
    session.add(instance)
    try:
        session.flush()
        create_instance_nodes(session, instance, result["runtime_resolution"])
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        concurrent = session.exec(
            select(WorkflowInstance).where(
                WorkflowInstance.namespace_id == namespace_id,
                WorkflowInstance.idempotency_key == idempotency_key,
            )
        ).first()
        if (
            concurrent
            and concurrent.project_id == project_id
            and concurrent.template_version_id == version.id
            and concurrent.input == body.input
            and concurrent.title == body.title
            and concurrent.default_runtime_id
            == (body.runtime_id or project.default_runtime_id)
        ):
            return _instance_public(session, concurrent)
        raise HTTPException(409, "Workflow task creation conflict") from exc
    session.refresh(instance)
    return _instance_public(session, instance)


@router.get("/workflow-instances/{instance_id}")
def read_workflow_instance(
    instance_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    reconcile_instance(session, instance)
    session.refresh(instance)
    return _instance_public(session, instance)


@router.post("/workflow-instances/{instance_id}/cancel")
def cancel_workflow_instance(
    instance_id: uuid.UUID,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    require_mutable_instance(instance)
    instance.status = WorkflowInstanceStatus.CANCELLED
    instance.cancelled_at = utcnow()
    instance.updated_at = utcnow()
    append_event(
        session, instance.id, "task_cancelled", {"user_id": str(current_user.id)}
    )
    session.add(instance)
    session.commit()
    return _instance_public(session, instance)


@router.get("/workflow-instances/{instance_id}/events", response_model=None)
async def list_workflow_events(
    instance_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
    after_sequence: int = 0,
    accept: str | None = Header(default=None, alias="Accept"),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any] | StreamingResponse:
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    if accept and "text/event-stream" in accept:
        try:
            cursor = (
                max(after_sequence, int(last_event_id))
                if last_event_id
                else after_sequence
            )
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID must be an integer") from exc

        async def generate() -> AsyncIterator[str]:
            nonlocal cursor
            while not await request.is_disconnected():
                session.expire_all()
                events = session.exec(
                    select(WorkflowEvent)
                    .where(
                        WorkflowEvent.workflow_instance_id == instance.id,
                        WorkflowEvent.sequence > cursor,
                    )
                    .order_by(col(WorkflowEvent.sequence))
                ).all()
                for event in events:
                    cursor = event.sequence
                    data = json.dumps(
                        {
                            "sequence": event.sequence,
                            "event_type": event.event_type,
                            "payload": event.payload,
                        },
                        ensure_ascii=False,
                    )
                    yield (
                        f"id: {event.sequence}\n"
                        f"event: {event.event_type}\n"
                        f"data: {data}\n\n"
                    )
                current = session.get(WorkflowInstance, instance.id)
                if current is None:
                    break
                if (
                    current.status
                    in {
                        WorkflowInstanceStatus.COMPLETED,
                        WorkflowInstanceStatus.CANCELLED,
                        WorkflowInstanceStatus.FAILED,
                    }
                    and not events
                ):
                    break
                if not events:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(generate(), media_type="text/event-stream")
    rows = session.exec(
        select(WorkflowEvent)
        .where(
            WorkflowEvent.workflow_instance_id == instance.id,
            WorkflowEvent.sequence > after_sequence,
        )
        .order_by(col(WorkflowEvent.sequence))
    ).all()
    return {"data": rows, "count": len(rows)}


@router.get("/workflow-instances/{instance_id}/nodes/{node_key}")
def read_workflow_node(
    instance_id: uuid.UUID,
    node_key: str,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    reconcile_instance(session, instance)
    node, definition = get_node(session, instance.id, node_key)
    revisions = session.exec(
        select(WorkflowNodeRevision)
        .where(WorkflowNodeRevision.node_instance_id == node.id)
        .order_by(col(WorkflowNodeRevision.revision))
    ).all()
    executions = session.exec(
        select(WorkflowNodeExecution)
        .where(WorkflowNodeExecution.node_instance_id == node.id)
        .order_by(col(WorkflowNodeExecution.attempt))
    ).all()
    confirmations = session.exec(
        select(WorkflowConfirmation)
        .where(WorkflowConfirmation.node_instance_id == node.id)
        .order_by(col(WorkflowConfirmation.created_at))
    ).all()
    return {
        "node": node,
        "definition": definition,
        "revisions": revisions,
        "executions": executions,
        "confirmations": confirmations,
    }


@router.post("/workflow-instances/{instance_id}/nodes/{node_key}/submit")
def submit_workflow_node(
    instance_id: uuid.UUID,
    node_key: str,
    body: NodeMutation,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    require_mutable_instance(instance)
    node, definition = get_node(session, instance.id, node_key)
    require_expected_revision(node, body.expected_revision)
    if (
        definition.confirmation_mode != ConfirmationMode.PROCESS
        and node.expected_revision == 0
    ):
        raise HTTPException(
            409, "Only process-confirmation nodes accept an initial manual submit"
        )
    if node.status not in {
        WorkflowNodeStatus.WAITING_CONFIRMATION,
        WorkflowNodeStatus.COMPLETED,
        WorkflowNodeStatus.UPDATE_REQUIRED,
        WorkflowNodeStatus.BLOCKED,
    }:
        raise HTTPException(409, "Node is not accepting a manual result")
    revision = submit_node(
        session,
        instance,
        node,
        definition,
        body.output,
        body.reason,
        current_user.id,
    )
    session.commit()
    return {
        "node": node,
        "revision": revision,
        "task": _instance_public(session, instance),
    }


@router.post("/workflow-instances/{instance_id}/nodes/{node_key}/confirm")
def confirm_workflow_node(
    instance_id: uuid.UUID,
    node_key: str,
    body: NodeConfirmation,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    require_mutable_instance(instance)
    node, definition = get_node(session, instance.id, node_key)
    require_expected_revision(node, body.expected_revision)
    if (
        definition.confirmation_mode != ConfirmationMode.RESULT
        or node.status != WorkflowNodeStatus.WAITING_CONFIRMATION
        or body.decision
        not in {ConfirmationDecision.ACCEPT, ConfirmationDecision.REJECT}
    ):
        raise HTTPException(409, "Node does not have a confirmable candidate result")
    session.add(
        WorkflowConfirmation(
            node_instance_id=node.id,
            revision=node.expected_revision,
            mode=definition.confirmation_mode,
            decision=body.decision,
            user_id=current_user.id,
            reason=body.reason,
        )
    )
    if body.decision == ConfirmationDecision.ACCEPT:
        node.status = WorkflowNodeStatus.COMPLETED
        append_event(
            session,
            instance.id,
            "node_confirmed",
            {"node_key": node.node_key, "revision": node.expected_revision},
        )
    else:
        node.status = WorkflowNodeStatus.UPDATE_REQUIRED
        append_event(
            session,
            instance.id,
            "node_rejected",
            {"node_key": node.node_key, "revision": node.expected_revision},
        )
    session.add(node)
    advance_instance(session, instance)
    session.commit()
    return {"node": node, "task": _instance_public(session, instance)}


@router.post("/workflow-instances/{instance_id}/nodes/{node_key}/skip")
def skip_workflow_node(
    instance_id: uuid.UUID,
    node_key: str,
    body: NodeSkip,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    require_mutable_instance(instance)
    node, definition = get_node(session, instance.id, node_key)
    require_expected_revision(node, body.expected_revision)
    if not definition.skippable:
        raise HTTPException(
            409, "Workflow Template does not allow this node to be skipped"
        )
    version = session.get(WorkflowTemplateVersion, instance.template_version_id)
    assert version is not None
    from app.workflow_management.engine import _definitions

    _, edges = _definitions(session, version.id)
    input_snapshot = build_node_input(session, instance, node, edges)
    revision = append_revision(
        session,
        node,
        input_snapshot,
        definition.skip_output,
        body.reason,
        current_user.id,
        skipped=True,
    )
    node.status = WorkflowNodeStatus.SKIPPED
    session.add(
        WorkflowConfirmation(
            node_instance_id=node.id,
            revision=revision.revision,
            mode=definition.confirmation_mode,
            decision=ConfirmationDecision.SKIP,
            user_id=current_user.id,
            reason=body.reason,
        )
    )
    append_event(
        session,
        instance.id,
        "node_skipped",
        {"node_key": node.node_key, "revision": revision.revision},
    )
    advance_instance(session, instance)
    session.commit()
    return {
        "node": node,
        "revision": revision,
        "task": _instance_public(session, instance),
    }


@router.post("/workflow-instances/{instance_id}/nodes/{node_key}/retry")
def retry_workflow_node(
    instance_id: uuid.UUID,
    node_key: str,
    body: NodeRetry,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    require_mutable_instance(instance)
    node, definition = get_node(session, instance.id, node_key)
    require_expected_revision(node, body.expected_revision)
    if node.status not in {
        WorkflowNodeStatus.FAILED,
        WorkflowNodeStatus.BLOCKED,
        WorkflowNodeStatus.NEEDS_MANUAL_RESOLUTION,
    }:
        raise HTTPException(409, "Only failed or blocked nodes can be retried")
    if definition.side_effecting:
        uncertain = session.exec(
            select(WorkflowNodeExecution).where(
                WorkflowNodeExecution.node_instance_id == node.id,
                WorkflowNodeExecution.status
                == WorkflowExecutionStatus.NEEDS_MANUAL_RESOLUTION,
            )
        ).first()
        if uncertain and (
            not uncertain.external_state_proof
            or not uncertain.external_state_proof.get("allow_retry", False)
        ):
            raise HTTPException(
                409,
                {
                    "code": "external_state_unknown",
                    "message": "Record a verified external-state resolution before retrying",
                },
            )
    node.status = WorkflowNodeStatus.UPDATE_REQUIRED
    instance.status = WorkflowInstanceStatus.RUNNING
    session.add_all([node, instance])
    append_event(
        session, instance.id, "node_retry_requested", {"node_key": node.node_key}
    )
    advance_instance(session, instance)
    session.commit()
    return {"node": node, "task": _instance_public(session, instance)}


@router.post(
    "/workflow-instances/{instance_id}/nodes/{node_key}/external-state-resolution"
)
def resolve_external_state(
    instance_id: uuid.UUID,
    node_key: str,
    body: ExternalStateResolution,
    session: SessionDep,
    current_user: CurrentUser,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key is required")
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    if not can_manage_namespace(session, namespace_id, current_user):
        raise HTTPException(403, "Namespace manager role required")
    require_mutable_instance(instance)
    node, definition = get_node(session, instance.id, node_key)
    require_expected_revision(node, body.expected_revision)
    if not definition.side_effecting:
        raise HTTPException(409, "Node does not declare external side effects")
    execution = session.get(WorkflowNodeExecution, body.execution_id)
    if execution is None or execution.node_instance_id != node.id:
        raise HTTPException(404, "Node execution not found")
    if execution.status != WorkflowExecutionStatus.NEEDS_MANUAL_RESOLUTION:
        raise HTTPException(409, "Execution does not require manual resolution")
    existing_key = (
        execution.external_state_proof.get("idempotency_key")
        if execution.external_state_proof
        else None
    )
    if existing_key:
        if existing_key != idempotency_key:
            raise HTTPException(409, "External state was already resolved")
        return {"node": node, "execution": execution}
    if body.allow_retry and body.conclusion not in {"not_started", "compensated"}:
        raise HTTPException(
            422, "Retry is only safe after proving not_started or compensated"
        )
    execution.external_state_proof = {
        "idempotency_key": idempotency_key,
        "conclusion": body.conclusion,
        "allow_retry": body.allow_retry,
        "evidence": body.evidence,
        "rationale": body.rationale,
        "verified_by": str(current_user.id),
        "verified_at": utcnow().isoformat(),
    }
    session.add(execution)
    append_event(
        session,
        instance.id,
        "external_state_resolved",
        {
            "node_key": node.node_key,
            "execution_id": str(execution.id),
            "conclusion": body.conclusion,
            "allow_retry": body.allow_retry,
            "verified_by": str(current_user.id),
        },
    )
    session.commit()
    session.refresh(execution)
    return {"node": node, "execution": execution}


@router.post(
    "/workflow-instances/{instance_id}/nodes/{node_key}/messages", status_code=202
)
def send_agent_node_message(
    instance_id: uuid.UUID,
    node_key: str,
    body: NodeMessage,
    session: SessionDep,
    current_user: CurrentUser,
    namespace_id: uuid.UUID = Depends(require_namespace_member),
) -> dict[str, Any]:
    instance = get_instance(session, instance_id, namespace_id)
    require_project_member(session, instance.project_id, namespace_id, current_user)
    require_mutable_instance(instance)
    node, definition = get_node(session, instance.id, node_key)
    require_expected_revision(node, body.expected_revision)
    if definition.node_type != WorkflowNodeType.AGENT:
        raise HTTPException(409, "Messages are only available for Agent nodes")
    execution = session.exec(
        select(WorkflowNodeExecution)
        .where(
            WorkflowNodeExecution.node_instance_id == node.id,
            col(WorkflowNodeExecution.conversation_id).is_not(None),
        )
        .order_by(col(WorkflowNodeExecution.attempt).desc())
    ).first()
    conversation = (
        session.get(Conversation, execution.conversation_id)
        if execution and execution.conversation_id
        else None
    )
    if conversation is None:
        raise HTTPException(409, "Agent node conversation has not started")
    participant = session.exec(
        select(ConversationAgent).where(
            ConversationAgent.conversation_id == conversation.id
        )
    ).one()
    message = ConversationMessage(
        conversation_id=conversation.id,
        sequence=next_message_sequence(session, conversation.id),
        author_type=MessageAuthorType.USER,
        author_id=current_user.id,
        target_type=MessageTargetType.MAIN,
        payload={"content": body.content},
        status=MessageStatus.RUNNING,
        idempotency_key=f"workflow-node-message:{uuid.uuid4()}",
    )
    session.add(message)
    session.flush()
    task = create_agent_task(session, conversation, participant, message, body.content)
    message.task_id = task.id
    session.add(message)
    session.commit()
    return {"message": message, "task_id": task.id}
