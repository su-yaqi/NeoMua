import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crud
from app.core.config import settings
from app.models import Namespace, NamespaceCreate, NamespaceRole
from app.project_management.models import (
    Project,
    ProjectMember,
    ProjectRepository,
    RepositoryStatus,
)
from app.runtime.models import RuntimeProfile, RuntimeRouteMode, RuntimeType
from app.workflow_management.models import (
    NamespaceWorkflowEnablement,
    WorkflowTemplate,
    WorkflowTemplateVersion,
)
from app.workflow_management.package import PackageValidationError, validate_package
from tests.utils.user import authentication_token_from_email, create_random_user
from tests.utils.utils import random_lower_string


def _manifest() -> dict:
    path = Path(__file__).parents[3] / "workflow_apps/project_delivery/manifest.json"
    return json.loads(path.read_text())


def test_package_validator_rejects_cycles() -> None:
    manifest = _manifest()
    manifest["edges"].append(
        {"source": "prepare", "target": "clarify", "condition_key": "always"}
    )
    try:
        validate_package(manifest)
    except PackageValidationError as exc:
        assert any(error["code"] == "dag_cycle" for error in exc.errors)
    else:
        raise AssertionError("cyclic Workflow Package was accepted")


def test_project_task_runs_human_then_code_result_confirmation(
    client: TestClient, db: Session
) -> None:
    user = create_random_user(db)
    namespace = Namespace.model_validate(
        NamespaceCreate(
            name=f"workflow-ns-{random_lower_string()}",
            code=f"workflow-{random_lower_string()}",
            admin_user_id=user.id,
        ).model_dump(exclude={"admin_user_id"})
    )
    db.add(namespace)
    db.commit()
    db.refresh(namespace)
    crud.ensure_namespace_membership(
        session=db,
        user_id=user.id,
        namespace_id=namespace.id,
        role=NamespaceRole.ADMIN,
    )
    runtime = RuntimeProfile(
        namespace_id=namespace.id,
        runtime_type=RuntimeType.PLATFORM,
        route_mode=RuntimeRouteMode.PLATFORM_GATEWAY,
        model_id="test-model",
        config={"compatibility_verified": True},
    )
    db.add(runtime)
    db.flush()
    project = Project(
        namespace_id=namespace.id,
        slug="delivery-project",
        name="Delivery Project",
        default_runtime_id=runtime.id,
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id))
    db.add(
        ProjectRepository(
            project_id=project.id,
            remote_url="https://example.test/delivery.git",
            purpose="交付仓库",
            status=RepositoryStatus.AVAILABLE,
            validated_commit="a" * 40,
            runtime_workspace_refs={str(runtime.id): "workspace://delivery"},
        )
    )
    template = db.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "project-delivery")
    ).one()
    version = db.exec(
        select(WorkflowTemplateVersion).where(
            WorkflowTemplateVersion.template_id == template.id
        )
    ).one()
    db.add(
        NamespaceWorkflowEnablement(
            namespace_id=namespace.id,
            template_version_id=version.id,
            enabled=True,
            is_default=True,
            updated_by=user.id,
        )
    )
    db.commit()
    headers = {
        **authentication_token_from_email(client=client, email=user.email, db=db),
        "X-Namespace-Id": str(namespace.id),
        "Idempotency-Key": "workflow-create-1",
    }
    created = client.post(
        f"{settings.API_V1_STR}/projects/{project.id}/workflow-instances",
        headers=headers,
        json={
            "title": "完成 V0.6 验收",
            "template_version_id": str(version.id),
            "input": {"request": "完成需求"},
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    clarify = next(node for node in task["nodes"] if node["node_key"] == "clarify")
    assert clarify["status"] == "waiting_confirmation"

    submitted = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/nodes/clarify/submit",
        headers=headers,
        json={
            "expected_revision": 0,
            "output": {
                "goals": ["完成工作台"],
                "acceptance_criteria": ["测试通过"],
            },
            "reason": "澄清完成",
        },
    )
    assert submitted.status_code == 200, submitted.text
    prepared = next(
        node
        for node in submitted.json()["task"]["nodes"]
        if node["node_key"] == "prepare"
    )
    assert prepared["status"] == "waiting_confirmation"
    assert prepared["expected_revision"] == 1

    confirmed = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/nodes/prepare/confirm",
        headers=headers,
        json={"expected_revision": 1, "decision": "accept", "reason": "接受交付"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["task"]["status"] == "completed"

    events = client.get(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/events",
        headers={**headers, "Accept": "text/event-stream"},
    )
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: task_completed" in events.text
    last_event_id = max(
        int(line.removeprefix("id: "))
        for line in events.text.splitlines()
        if line.startswith("id: ")
    )
    resumed = client.get(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/events",
        headers={
            **headers,
            "Accept": "text/event-stream",
            "Last-Event-ID": str(last_event_id),
        },
    )
    assert resumed.status_code == 200
    assert "data:" not in resumed.text
