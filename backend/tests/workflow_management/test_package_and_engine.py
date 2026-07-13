import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlmodel import Session, select
from workflow_runtime.executor import execute_runtime_job
from workflow_runtime.package import package_content_digest

from app import crud
from app.core.config import settings
from app.models import Namespace, NamespaceCreate, NamespaceRole
from app.project_management.models import (
    Project,
    ProjectMember,
    ProjectRepository,
    RepositoryStatus,
)
from app.runtime.models import (
    RuntimeJob,
    RuntimeJobKind,
    RuntimeJobStatus,
    RuntimeProfile,
    RuntimeRouteMode,
    RuntimeType,
)
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


def test_package_digest_covers_backend_and_frontend(tmp_path: Path) -> None:
    package_root = Path(__file__).parents[3] / "workflow_apps/project_delivery"
    manifest = _manifest()
    initial = package_content_digest(package_root, manifest)
    copied = tmp_path / "project_delivery"
    import shutil

    shutil.copytree(package_root, copied)
    backend_file = copied / "backend/nodes/prepare.py"
    backend_file.write_text(backend_file.read_text() + "\n# digest probe\n")
    assert package_content_digest(copied, manifest) != initial
    shutil.copytree(package_root, copied, dirs_exist_ok=True)
    frontend_file = copied / "frontend/ProjectDeliveryApplication.tsx"
    frontend_file.write_text(frontend_file.read_text() + "\n// digest probe\n")
    assert package_content_digest(copied, manifest) != initial


def _finish_queued_runtime_jobs(db: Session) -> int:
    jobs = db.exec(
        select(RuntimeJob).where(RuntimeJob.status == RuntimeJobStatus.QUEUED)
    ).all()
    for job in jobs:
        job.status = RuntimeJobStatus.SUCCEEDED
        job.result = execute_runtime_job(job.kind.value, job.payload)
        job.completed_at = datetime.now(timezone.utc)
        db.add(job)
    db.commit()
    return len(jobs)


def test_project_task_runs_human_then_code_result_confirmation(
    client: TestClient, db: Session, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ARTIFACT_LOCAL_ROOT", str(tmp_path / "artifacts"))
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
    repository = ProjectRepository(
        project_id=project.id,
        remote_url="https://example.test/delivery.git",
        purpose="交付仓库",
        status=RepositoryStatus.AVAILABLE,
        validated_commit="a" * 40,
    )
    db.add(repository)
    db.flush()
    validation_job = RuntimeJob(
        namespace_id=namespace.id,
        runtime_profile_id=runtime.id,
        kind=RuntimeJobKind.REPOSITORY_PROBE,
        status=RuntimeJobStatus.SUCCEEDED,
        payload={},
        result={
            "workspace_ref": "workspace://delivery",
            "remote_url": repository.remote_url,
            "commit": repository.validated_commit,
            "spec_locations": [],
        },
        idempotency_key=f"test-repository-proof:{repository.id}",
        completed_at=datetime.now(timezone.utc),
    )
    db.add(validation_job)
    db.flush()
    repository.validation_job_id = validation_job.id
    repository.runtime_workspace_refs = {
        str(runtime.id): {
            "workspace_ref": "workspace://delivery",
            "remote_url": repository.remote_url,
            "commit": repository.validated_commit,
            "runtime_job_id": str(validation_job.id),
            "spec_locations": [],
        }
    }
    db.add(repository)
    template = db.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "project-delivery")
    ).one()
    version = db.exec(
        select(WorkflowTemplateVersion).where(
            WorkflowTemplateVersion.template_id == template.id,
            WorkflowTemplateVersion.version == _manifest()["version"],
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
    assert (
        task["application"]["component_key"]
        == _manifest()["application"]["component_key"]
    )
    uploaded = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/attachments",
        headers=headers,
        files={"file": ("acceptance.md", "# 验收\n必须可恢复", "text/markdown")},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["scan_status"] == "clean"
    assert "storage_ref" not in uploaded.json()
    listed_attachments = client.get(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/attachments",
        headers=headers,
    )
    assert listed_attachments.status_code == 200
    assert listed_attachments.json()["data"][0]["filename"] == "acceptance.md"
    unsafe_attachment = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/attachments",
        headers=headers,
        files={"file": ("unsafe.txt", b"unsafe\x00data", "text/plain")},
    )
    assert unsafe_attachment.status_code == 422
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
    stale_submit = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/nodes/clarify/submit",
        headers=headers,
        json={
            "expected_revision": 0,
            "output": {
                "goals": ["覆盖他人结果"],
                "acceptance_criteria": ["不应成功"],
            },
            "reason": "过期修订",
        },
    )
    assert stale_submit.status_code == 409
    prepared = next(
        node
        for node in submitted.json()["task"]["nodes"]
        if node["node_key"] == "prepare"
    )
    assert prepared["status"] == "running"

    assert _finish_queued_runtime_jobs(db) == 1
    reconciled = client.get(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}", headers=headers
    )
    assert reconciled.status_code == 200, reconciled.text
    assert _finish_queued_runtime_jobs(db) == 1
    reconciled = client.get(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}", headers=headers
    )
    assert reconciled.status_code == 200, reconciled.text
    prepared = next(
        node for node in reconciled.json()["nodes"] if node["node_key"] == "prepare"
    )
    assert prepared["status"] == "waiting_confirmation"
    assert prepared["expected_revision"] == 1
    forbidden_skip = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/nodes/prepare/skip",
        headers=headers,
        json={"expected_revision": 1, "reason": "模板未允许跳过"},
    )
    assert forbidden_skip.status_code == 409

    revised = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/nodes/clarify/submit",
        headers=headers,
        json={
            "expected_revision": 1,
            "output": {
                "goals": ["完成工作台", "保留历史"],
                "acceptance_criteria": ["测试通过", "下游自动重跑"],
            },
            "reason": "补充验收要求",
        },
    )
    assert revised.status_code == 200, revised.text
    prepared = next(
        node
        for node in revised.json()["task"]["nodes"]
        if node["node_key"] == "prepare"
    )
    assert prepared["status"] == "running"
    assert prepared["expected_revision"] == 1
    assert _finish_queued_runtime_jobs(db) == 1
    assert (
        client.get(
            f"{settings.API_V1_STR}/workflow-instances/{task['id']}", headers=headers
        ).status_code
        == 200
    )
    assert _finish_queued_runtime_jobs(db) == 1
    rerun = client.get(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}", headers=headers
    )
    assert rerun.status_code == 200, rerun.text
    prepared = next(
        node for node in rerun.json()["nodes"] if node["node_key"] == "prepare"
    )
    assert prepared["status"] == "waiting_confirmation"
    assert prepared["expected_revision"] == 2
    prepare_history = client.get(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/nodes/prepare",
        headers=headers,
    )
    assert prepare_history.status_code == 200
    assert [item["revision"] for item in prepare_history.json()["revisions"]] == [
        1,
        2,
    ]

    confirmed = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/nodes/prepare/confirm",
        headers=headers,
        json={"expected_revision": 2, "decision": "accept", "reason": "接受交付"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["task"]["status"] == "completed"
    immutable_upload = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/attachments",
        headers=headers,
        files={"file": ("late.md", "# too late", "text/markdown")},
    )
    assert immutable_upload.status_code == 409
    immutable_submit = client.post(
        f"{settings.API_V1_STR}/workflow-instances/{task['id']}/nodes/clarify/submit",
        headers=headers,
        json={
            "expected_revision": 1,
            "output": {
                "goals": ["完成后不得修改"],
                "acceptance_criteria": ["只读"],
            },
            "reason": "完成后修改",
        },
    )
    assert immutable_submit.status_code == 409

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
