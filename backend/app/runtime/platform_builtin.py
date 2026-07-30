"""Persistent system-owned platform Runtime and provider-route reconciliation."""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.config import settings
from app.llm_provider_service import sanitize_error_text
from app.models import LlmProviderConfig, LlmProviderModel, ProviderValidationStatus
from app.runtime.catalog import RuntimeCatalogError, canonical_digest
from app.runtime.connections import node_is_online
from app.runtime.models import (
    LlmProviderModelValidation,
    NodeInstallationReceipt,
    PlatformRuntimeReconcileAttempt,
    PlatformRuntimeReconcileJob,
    ReconcileStatus,
    RuntimeCapabilityReport,
    RuntimeConfigurationOrigin,
    RuntimeConfigurationRevision,
    RuntimeConfigurationStatus,
    RuntimeEngineType,
    RuntimeInstance,
    RuntimeInstanceStatus,
    RuntimeLocationType,
    RuntimeManagementType,
    RuntimeModelBinding,
    RuntimeModelBindingOrigin,
    RuntimeModelBindingStatus,
    RuntimeModelRouteType,
    RuntimeNode,
)
from app.runtime.provider_validation import validate_provider_model_call

PLATFORM_RUNTIME_SOURCE_KEY = "platform:claude-agent-sdk"
PLATFORM_RUNTIME_INSTALLATION_KEY = "platform:claude-agent-sdk"
PLATFORM_ADAPTER_VERSION = "1.0.0"
VALIDATION_TTL = timedelta(hours=24)


def _provider_configuration_fingerprint(provider: LlmProviderConfig) -> str:
    secret_digest = (
        hashlib.sha256(provider.secret_ciphertext.encode()).hexdigest()
        if provider.secret_ciphertext
        else None
    )
    return canonical_digest(
        {
            "provider_config_id": str(provider.id),
            "provider_slug": provider.provider_slug,
            "base_url": provider.base_url,
            "secret_digest": secret_digest,
            "extra_config": provider.extra_config,
            "enabled": provider.enabled,
            "validation_status": provider.validation_status.value,
            "last_validated_at": (
                provider.last_validated_at.isoformat()
                if provider.last_validated_at
                else None
            ),
        }
    )


def _reconcile_input(session: Session, namespace_id: uuid.UUID) -> dict[str, Any]:
    runtime = session.exec(
        select(RuntimeInstance).where(
            RuntimeInstance.namespace_id == namespace_id,
            RuntimeInstance.management_type == RuntimeManagementType.PLATFORM_BUILTIN,
        )
    ).first()
    providers = session.exec(
        select(LlmProviderConfig)
        .where(LlmProviderConfig.namespace_id == namespace_id)
        .order_by(col(LlmProviderConfig.id))
    ).all()
    provider_inputs: list[dict[str, Any]] = []
    for provider in providers:
        models = session.exec(
            select(LlmProviderModel)
            .where(LlmProviderModel.provider_config_id == provider.id)
            .order_by(col(LlmProviderModel.id))
        ).all()
        provider_inputs.append(
            {
                "provider_id": str(provider.id),
                "configuration_fingerprint": _provider_configuration_fingerprint(
                    provider
                ),
                "models": [
                    {
                        "id": str(model.id),
                        "model_definition_id": (
                            str(model.model_definition_id)
                            if model.model_definition_id
                            else None
                        ),
                        "model_id": model.model_id,
                        "enabled": model.is_enabled,
                    }
                    for model in models
                ],
            }
        )
    return {
        "namespace_id": str(namespace_id),
        "deployment_fingerprint": settings.RUNTIME_WORKER_RELEASE_DIGEST,
        "runtime_id": str(runtime.id) if runtime else None,
        "runtime_status": runtime.status.value if runtime else None,
        "configuration_id": (
            str(runtime.applied_configuration_revision_id)
            if runtime and runtime.applied_configuration_revision_id
            else None
        ),
        "capability_id": (
            str(runtime.current_capability_report_id)
            if runtime and runtime.current_capability_report_id
            else None
        ),
        "providers": provider_inputs,
    }


def enqueue_platform_reconcile_job(
    session: Session, namespace_id: uuid.UUID, *, trigger: str
) -> PlatformRuntimeReconcileJob:
    input_fingerprint = canonical_digest(_reconcile_input(session, namespace_id))
    existing = session.exec(
        select(PlatformRuntimeReconcileJob).where(
            PlatformRuntimeReconcileJob.namespace_id == namespace_id,
            PlatformRuntimeReconcileJob.input_fingerprint == input_fingerprint,
        )
    ).first()
    if existing is not None:
        if existing.status == ReconcileStatus.FAILED:
            existing.status = ReconcileStatus.QUEUED
            existing.completed_at = None
            existing.updated_at = datetime.now(timezone.utc)
            session.add(existing)
        return existing
    job = PlatformRuntimeReconcileJob(
        namespace_id=namespace_id,
        trigger=trigger,
        input_fingerprint=input_fingerprint,
        status=ReconcileStatus.QUEUED,
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        existing = session.exec(
            select(PlatformRuntimeReconcileJob).where(
                PlatformRuntimeReconcileJob.namespace_id == namespace_id,
                PlatformRuntimeReconcileJob.input_fingerprint == input_fingerprint,
            )
        ).first()
        if existing is None:
            raise
        return existing
    return job


def ensure_builtin_platform_runtime(
    session: Session, namespace_id: uuid.UUID
) -> RuntimeInstance:
    runtime = session.exec(
        select(RuntimeInstance).where(
            RuntimeInstance.namespace_id == namespace_id,
            RuntimeInstance.management_type == RuntimeManagementType.PLATFORM_BUILTIN,
        )
    ).first()
    if runtime is not None:
        return runtime

    runtime = RuntimeInstance(
        namespace_id=namespace_id,
        location_type=RuntimeLocationType.PLATFORM,
        management_type=RuntimeManagementType.PLATFORM_BUILTIN,
        lifecycle_source_key=PLATFORM_RUNTIME_SOURCE_KEY,
        name="平台内置 Claude Agent SDK",
        installation_key=PLATFORM_RUNTIME_INSTALLATION_KEY,
        engine_type=RuntimeEngineType.CLAUDE_AGENT_SDK,
        adapter_version=PLATFORM_ADAPTER_VERSION,
        status=RuntimeInstanceStatus.DISCOVERED,
        enabled=True,
    )
    payload = {
        "executable": "claude",
        "arguments": [],
        "working_directory_policy": "workspace",
        "environment_allowlist": [],
        "security_policy": {"permission_modes": ["default", "acceptEdits", "plan"]},
        "resource_limits": {"max_timeout_seconds": 3600},
    }
    try:
        with session.begin_nested():
            session.add(runtime)
            session.flush()
            configuration = RuntimeConfigurationRevision(
                runtime_instance_id=runtime.id,
                revision=1,
                origin=RuntimeConfigurationOrigin.SYSTEM_BUILTIN,
                adapter_execution_ref=PLATFORM_RUNTIME_SOURCE_KEY,
                configuration_digest=canonical_digest(payload),
                status=RuntimeConfigurationStatus.DESIRED,
                created_by=None,
                executable=payload["executable"],
                arguments=payload["arguments"],
                working_directory_policy=payload["working_directory_policy"],
                environment_allowlist=payload["environment_allowlist"],
                security_policy=payload["security_policy"],
                resource_limits=payload["resource_limits"],
            )
            session.add(configuration)
            session.flush()
            runtime.desired_configuration_revision_id = configuration.id
            session.add(runtime)
    except IntegrityError:
        existing = session.exec(
            select(RuntimeInstance).where(
                RuntimeInstance.namespace_id == namespace_id,
                RuntimeInstance.management_type
                == RuntimeManagementType.PLATFORM_BUILTIN,
            )
        ).first()
        if existing is None:
            raise
        return existing
    return runtime


def _latest_valid_model_validation(
    session: Session,
    *,
    provider_model_id: uuid.UUID,
    configuration_fingerprint: str,
    now: datetime,
) -> LlmProviderModelValidation | None:
    return session.exec(
        select(LlmProviderModelValidation)
        .where(
            LlmProviderModelValidation.provider_model_id == provider_model_id,
            LlmProviderModelValidation.configuration_fingerprint
            == configuration_fingerprint,
            LlmProviderModelValidation.status == "succeeded",
            col(LlmProviderModelValidation.valid_until) > now,
        )
        .order_by(col(LlmProviderModelValidation.completed_at).desc())
    ).first()


def _validate_provider_model(
    session: Session,
    *,
    namespace_id: uuid.UUID,
    provider: LlmProviderConfig,
    model: LlmProviderModel,
    now: datetime,
) -> LlmProviderModelValidation:
    configuration_fingerprint = _provider_configuration_fingerprint(provider)
    current = _latest_valid_model_validation(
        session,
        provider_model_id=model.id,
        configuration_fingerprint=configuration_fingerprint,
        now=now,
    )
    if current is not None:
        return current
    attempt_no = (
        len(
            session.exec(
                select(LlmProviderModelValidation).where(
                    LlmProviderModelValidation.provider_model_id == model.id,
                    LlmProviderModelValidation.configuration_fingerprint
                    == configuration_fingerprint,
                )
            ).all()
        )
        + 1
    )
    validation = LlmProviderModelValidation(
        namespace_id=namespace_id,
        provider_config_id=provider.id,
        provider_model_id=model.id,
        configuration_fingerprint=configuration_fingerprint,
        attempt_no=attempt_no,
        status="running",
        started_at=now,
    )
    session.add(validation)
    session.flush()
    try:
        evidence = validate_provider_model_call(provider, model.model_id)
    except RuntimeCatalogError as exc:
        validation.status = "failed"
        validation.error = {"code": exc.code, "message": exc.message}
        validation.completed_at = datetime.now(timezone.utc)
    except Exception as exc:
        validation.status = "failed"
        validation.error = {
            "code": "provider_model_validation_failed",
            "message": sanitize_error_text(str(exc)),
        }
        validation.completed_at = datetime.now(timezone.utc)
    else:
        validation.status = "succeeded"
        validation.evidence = evidence
        validation.evidence_digest = canonical_digest(evidence)
        validation.completed_at = datetime.now(timezone.utc)
        validation.valid_until = validation.completed_at + VALIDATION_TTL
    session.add(validation)
    return validation


def reconcile_platform_model_bindings(
    session: Session, namespace_id: uuid.UUID
) -> RuntimeInstance:
    runtime = ensure_builtin_platform_runtime(session, namespace_id)
    capability = (
        session.get(RuntimeCapabilityReport, runtime.current_capability_report_id)
        if runtime.current_capability_report_id
        else None
    )
    now = datetime.now(timezone.utc)
    valid_routes: set[tuple[uuid.UUID, uuid.UUID]] = set()
    providers = session.exec(
        select(LlmProviderConfig).where(LlmProviderConfig.namespace_id == namespace_id)
    ).all()
    for provider in providers:
        configuration_fingerprint = _provider_configuration_fingerprint(provider)
        models = session.exec(
            select(LlmProviderModel).where(
                LlmProviderModel.provider_config_id == provider.id
            )
        ).all()
        for model in models:
            if model.model_definition_id is None:
                continue
            valid_routes.add((provider.id, model.id))
            binding = session.exec(
                select(RuntimeModelBinding).where(
                    RuntimeModelBinding.runtime_instance_id == runtime.id,
                    RuntimeModelBinding.provider_config_id == provider.id,
                    RuntimeModelBinding.provider_model_id == model.id,
                    RuntimeModelBinding.origin
                    == RuntimeModelBindingOrigin.LLM_CONFIG_RECONCILED,
                )
            ).first()
            if binding is None:
                binding = RuntimeModelBinding(
                    namespace_id=namespace_id,
                    runtime_instance_id=runtime.id,
                    origin=RuntimeModelBindingOrigin.LLM_CONFIG_RECONCILED,
                    model_definition_id=model.model_definition_id,
                    provider_config_id=provider.id,
                    provider_model_id=model.id,
                    route_type=RuntimeModelRouteType.PROVIDER_CONFIG,
                    route_key=f"provider:{provider.id}:{model.id}",
                    engine_model_id=model.model_id,
                )
            binding.model_definition_id = model.model_definition_id
            binding.engine_model_id = model.model_id
            validation = _latest_valid_model_validation(
                session,
                provider_model_id=model.id,
                configuration_fingerprint=configuration_fingerprint,
                now=now,
            )
            provider_ready = bool(
                provider.enabled
                and provider.validation_status == ProviderValidationStatus.SUCCESS
                and provider.last_validated_at is not None
                and provider.last_validated_at + VALIDATION_TTL > now
                and model.is_enabled
            )
            runtime_ready = bool(
                runtime.status == RuntimeInstanceStatus.AVAILABLE
                and runtime.applied_configuration_revision_id
                and capability is not None
                and settings.RUNTIME_WORKER_RELEASE_DIGEST is not None
                and capability.capabilities.get("release_digest")
                == settings.RUNTIME_WORKER_RELEASE_DIGEST
            )
            if (
                provider_ready
                and validation is not None
                and runtime_ready
                and capability
            ):
                binding.status = RuntimeModelBindingStatus.AVAILABLE
                binding.provider_model_validation_id = validation.id
                binding.validation_fingerprint = validation.evidence_digest
                binding.validated_capability_fingerprint = (
                    capability.capability_fingerprint
                )
                binding.last_validated_at = validation.completed_at
                binding.validation_expires_at = validation.valid_until
                binding.last_error = None
            else:
                binding.status = RuntimeModelBindingStatus.DECLARED
                binding.provider_model_validation_id = (
                    validation.id if validation is not None else None
                )
                binding.validation_fingerprint = None
                binding.validated_capability_fingerprint = None
                binding.validation_expires_at = None
                if not provider_ready:
                    binding.last_validated_at = None
                    binding.last_error = {"code": "provider_model_not_validated"}
                elif validation is None:
                    binding.last_error = {"code": "provider_model_route_not_validated"}
                else:
                    binding.last_error = {
                        "code": (
                            "platform_runtime_release_untrusted"
                            if capability is not None
                            and (
                                settings.RUNTIME_WORKER_RELEASE_DIGEST is None
                                or capability.capabilities.get("release_digest")
                                != settings.RUNTIME_WORKER_RELEASE_DIGEST
                            )
                            else "platform_runtime_not_ready"
                        )
                    }
            binding.updated_at = now
            session.add(binding)

    automatic = session.exec(
        select(RuntimeModelBinding).where(
            RuntimeModelBinding.runtime_instance_id == runtime.id,
            RuntimeModelBinding.origin
            == RuntimeModelBindingOrigin.LLM_CONFIG_RECONCILED,
        )
    ).all()
    for binding in automatic:
        route = (binding.provider_config_id, binding.provider_model_id)
        if route not in valid_routes:
            binding.status = RuntimeModelBindingStatus.DISABLED
            binding.last_error = {"code": "provider_model_removed"}
            binding.updated_at = now
            session.add(binding)
    reconcile_service_model_bindings(session, namespace_id)
    return runtime


def reconcile_service_model_bindings(session: Session, namespace_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    providers = session.exec(
        select(LlmProviderConfig).where(LlmProviderConfig.namespace_id == namespace_id)
    ).all()
    services = session.exec(
        select(RuntimeInstance).where(
            RuntimeInstance.namespace_id == namespace_id,
            RuntimeInstance.management_type == RuntimeManagementType.SERVICE_MANAGED,
        )
    ).all()
    for runtime in services:
        configuration = (
            session.get(
                RuntimeConfigurationRevision,
                runtime.applied_configuration_revision_id,
            )
            if runtime.applied_configuration_revision_id is not None
            else None
        )
        capability = (
            session.get(RuntimeCapabilityReport, runtime.current_capability_report_id)
            if runtime.current_capability_report_id is not None
            else None
        )
        node = (
            session.get(RuntimeNode, runtime.runtime_node_id)
            if runtime.runtime_node_id is not None
            else None
        )
        receipt = (
            session.get(NodeInstallationReceipt, node.current_installation_receipt_id)
            if node is not None and node.current_installation_receipt_id is not None
            else None
        )
        runtime_ready = bool(
            runtime.enabled
            and configuration is not None
            and capability is not None
            and capability.configuration_digest == configuration.configuration_digest
            and capability.reported_at >= now - timedelta(minutes=5)
            and node is not None
            and node_is_online(node)
            and receipt is not None
            and capability.capabilities.get("distribution_manifest_digest")
            == receipt.manifest_digest
        )
        valid_routes: set[tuple[uuid.UUID, uuid.UUID]] = set()
        available_count = 0
        for provider in providers:
            configuration_fingerprint = _provider_configuration_fingerprint(provider)
            models = session.exec(
                select(LlmProviderModel).where(
                    LlmProviderModel.provider_config_id == provider.id
                )
            ).all()
            for model in models:
                if model.model_definition_id is None:
                    continue
                valid_routes.add((provider.id, model.id))
                binding = session.exec(
                    select(RuntimeModelBinding).where(
                        RuntimeModelBinding.runtime_instance_id == runtime.id,
                        RuntimeModelBinding.provider_config_id == provider.id,
                        RuntimeModelBinding.provider_model_id == model.id,
                        RuntimeModelBinding.origin
                        == RuntimeModelBindingOrigin.LLM_CONFIG_RECONCILED,
                    )
                ).first()
                if binding is None:
                    binding = RuntimeModelBinding(
                        namespace_id=namespace_id,
                        runtime_instance_id=runtime.id,
                        origin=RuntimeModelBindingOrigin.LLM_CONFIG_RECONCILED,
                        model_definition_id=model.model_definition_id,
                        provider_config_id=provider.id,
                        provider_model_id=model.id,
                        route_type=RuntimeModelRouteType.PROVIDER_CONFIG,
                        route_key=f"provider:{provider.id}:{model.id}",
                        engine_model_id=model.model_id,
                    )
                binding.model_definition_id = model.model_definition_id
                binding.engine_model_id = model.model_id
                validation = _latest_valid_model_validation(
                    session,
                    provider_model_id=model.id,
                    configuration_fingerprint=configuration_fingerprint,
                    now=now,
                )
                provider_ready = bool(
                    provider.enabled
                    and provider.validation_status == ProviderValidationStatus.SUCCESS
                    and provider.last_validated_at is not None
                    and provider.last_validated_at + VALIDATION_TTL > now
                    and model.is_enabled
                )
                if provider_ready and validation is not None and runtime_ready:
                    binding.status = RuntimeModelBindingStatus.AVAILABLE
                    binding.provider_model_validation_id = validation.id
                    binding.validation_fingerprint = validation.evidence_digest
                    binding.validated_capability_fingerprint = (
                        capability.capability_fingerprint if capability else None
                    )
                    binding.last_validated_at = validation.completed_at
                    binding.validation_expires_at = validation.valid_until
                    binding.last_error = None
                    available_count += 1
                else:
                    binding.status = RuntimeModelBindingStatus.DECLARED
                    binding.provider_model_validation_id = (
                        validation.id if validation else None
                    )
                    binding.validation_fingerprint = None
                    binding.validated_capability_fingerprint = None
                    binding.validation_expires_at = None
                    binding.last_error = {
                        "code": (
                            "provider_model_not_validated"
                            if not provider_ready or validation is None
                            else "service_runtime_not_ready"
                        )
                    }
                binding.updated_at = now
                session.add(binding)
        automatic = session.exec(
            select(RuntimeModelBinding).where(
                RuntimeModelBinding.runtime_instance_id == runtime.id,
                RuntimeModelBinding.origin
                == RuntimeModelBindingOrigin.LLM_CONFIG_RECONCILED,
            )
        ).all()
        for binding in automatic:
            if (
                binding.provider_config_id,
                binding.provider_model_id,
            ) not in valid_routes:
                binding.status = RuntimeModelBindingStatus.DISABLED
                binding.last_error = {"code": "provider_model_removed"}
                binding.updated_at = now
                session.add(binding)
        if runtime.status != RuntimeInstanceStatus.INCOMPATIBLE:
            runtime.status = (
                RuntimeInstanceStatus.AVAILABLE
                if runtime_ready and available_count > 0
                else RuntimeInstanceStatus.UNAVAILABLE
            )
            runtime.updated_at = now
            session.add(runtime)


def _process_reconcile_job(session: Session, job: PlatformRuntimeReconcileJob) -> None:
    now = datetime.now(timezone.utc)
    job.status = ReconcileStatus.RUNNING
    job.attempt_count += 1
    job.updated_at = now
    attempt = PlatformRuntimeReconcileAttempt(
        job_id=job.id,
        attempt_no=job.attempt_count,
        input_fingerprint=job.input_fingerprint,
        status=ReconcileStatus.RUNNING,
        started_at=now,
    )
    session.add(job)
    session.add(attempt)
    session.flush()
    try:
        runtime = ensure_builtin_platform_runtime(session, job.namespace_id)
        providers = session.exec(
            select(LlmProviderConfig).where(
                LlmProviderConfig.namespace_id == job.namespace_id
            )
        ).all()
        validation_count = 0
        for provider in providers:
            if (
                not provider.enabled
                or provider.validation_status != ProviderValidationStatus.SUCCESS
                or provider.last_validated_at is None
                or provider.last_validated_at + VALIDATION_TTL <= now
            ):
                continue
            models = session.exec(
                select(LlmProviderModel).where(
                    LlmProviderModel.provider_config_id == provider.id,
                    col(LlmProviderModel.is_enabled).is_(True),
                    col(LlmProviderModel.model_definition_id).is_not(None),
                )
            ).all()
            for model in models:
                validation = _validate_provider_model(
                    session,
                    namespace_id=job.namespace_id,
                    provider=provider,
                    model=model,
                    now=now,
                )
                validation_count += validation.status == "succeeded"
        reconcile_platform_model_bindings(session, job.namespace_id)
        attempt.status = ReconcileStatus.SUCCEEDED
        attempt.result = {
            "runtime_instance_id": str(runtime.id),
            "validated_model_count": validation_count,
        }
        job.status = ReconcileStatus.SUCCEEDED
        job.last_error = None
    except Exception as exc:
        error = {
            "code": "platform_runtime_reconcile_failed",
            "message": sanitize_error_text(str(exc)),
        }
        attempt.status = ReconcileStatus.FAILED
        attempt.error = error
        job.status = ReconcileStatus.FAILED
        job.last_error = error
    completed_at = datetime.now(timezone.utc)
    attempt.completed_at = completed_at
    job.completed_at = completed_at
    job.updated_at = completed_at
    session.add(attempt)
    session.add(job)


def process_platform_reconcile_jobs(session: Session, *, limit: int = 20) -> int:
    jobs = session.exec(
        select(PlatformRuntimeReconcileJob)
        .where(PlatformRuntimeReconcileJob.status == ReconcileStatus.QUEUED)
        .order_by(col(PlatformRuntimeReconcileJob.created_at))
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for job in jobs:
        _process_reconcile_job(session, job)
    return len(jobs)


def validate_and_reconcile_platform_model_bindings(
    session: Session, namespace_id: uuid.UUID
) -> RuntimeInstance:
    """Compatibility wrapper: persist a reconcile request without network I/O."""
    runtime = ensure_builtin_platform_runtime(session, namespace_id)
    enqueue_platform_reconcile_job(
        session, namespace_id, trigger="legacy_validation_request"
    )
    reconcile_platform_model_bindings(session, namespace_id)
    return runtime
