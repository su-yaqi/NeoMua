import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import col, select

from app import crud
from app.api.deps import CurrentUser, SessionDep, require_namespace_admin
from app.core.config import settings
from app.llm_provider_service import (
    fetch_provider_models,
    get_primary_secret_mask,
    get_provider_definition,
    list_provider_catalog_items,
    merge_provider_models,
    open_secret_payload,
    sanitize_error_text,
    seal_secret_payload,
    to_provider_config_public,
    validate_provider_connection,
    validate_provider_connection_inputs,
    validate_secret_inputs,
)
from app.models import (
    LlmProviderCatalogPublic,
    LlmProviderConfig,
    LlmProviderConfigCreate,
    LlmProviderConfigPublic,
    LlmProviderConfigsPublic,
    LlmProviderConfigUpdate,
    LlmProviderDraftRequest,
    LlmProviderDraftResult,
    LlmProviderModelPreview,
    LlmProviderSyncModelsRequest,
    ProviderValidationStatus,
)
from app.runtime.endpoints import EndpointValidationError, canonical_endpoint
from app.runtime.models import (
    LlmProviderModelValidation,
    PlatformRuntimeReconcileJob,
    RuntimeCapabilityReport,
    RuntimeInstance,
    RuntimeManagementType,
    RuntimeModelBinding,
    RuntimeModelBindingStatus,
)
from app.runtime.platform_builtin import (
    enqueue_platform_reconcile_job,
    reconcile_platform_model_bindings,
)

router = APIRouter(prefix="/llm", tags=["llm"])


class ProviderModelRuntimeReadiness(BaseModel):
    provider_model_id: uuid.UUID
    model_id: str
    enabled: bool
    model_definition_id: uuid.UUID | None
    validation_status: str
    validation_error: dict[str, Any] | None
    validation_completed_at: datetime | None
    validation_valid_until: datetime | None
    binding_id: uuid.UUID | None
    binding_status: str | None
    binding_error: dict[str, Any] | None
    ready: bool


class ProviderRuntimeReadiness(BaseModel):
    status: str
    expected_worker_release_digest: str | None
    reported_worker_release_digest: str | None
    release_trusted: bool
    runtime_instance_id: uuid.UUID | None
    runtime_status: str | None
    reconcile_job_id: uuid.UUID | None
    reconcile_status: str | None
    reconcile_trigger: str | None
    reconcile_attempt_count: int
    reconcile_error: dict[str, Any] | None
    models: list[ProviderModelRuntimeReadiness]


def _require_namespace_config(
    *,
    session: SessionDep,
    config_id: uuid.UUID,
    namespace_id: uuid.UUID,
) -> LlmProviderConfig:
    config = crud.get_llm_provider_config(session=session, config_id=config_id)
    if config is None or config.namespace_id != namespace_id:
        raise HTTPException(status_code=404, detail="LLM provider config not found")
    return config


def _prepare_draft_provider(
    draft_in: LlmProviderDraftRequest,
) -> tuple[Any, str, dict[str, str]]:
    try:
        definition = get_provider_definition(draft_in.provider_slug)
        secret_inputs = validate_secret_inputs(definition, draft_in.secret_inputs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        base_url = canonical_endpoint(draft_in.base_url)
    except EndpointValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return definition, base_url, secret_inputs


@router.get("/providers/catalog", response_model=LlmProviderCatalogPublic)
def read_provider_catalog(
    _: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    data = list_provider_catalog_items()
    return LlmProviderCatalogPublic(data=data, count=len(data))


@router.get("/provider-configs", response_model=LlmProviderConfigsPublic)
def read_provider_configs(
    session: SessionDep,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    configs = crud.list_llm_provider_configs(session=session, namespace_id=namespace_id)
    return LlmProviderConfigsPublic(
        data=[to_provider_config_public(config) for config in configs],
        count=len(configs),
    )


@router.post("/provider-configs", response_model=LlmProviderConfigPublic)
def create_provider_config(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    config_in: LlmProviderConfigCreate,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    definition = get_provider_definition(config_in.provider_slug)
    try:
        secret_inputs = validate_secret_inputs(definition, config_in.secret_inputs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        base_url = canonical_endpoint(config_in.base_url)
    except EndpointValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    discovered_models: list[dict[str, Any]] | None = None
    validation_status = ProviderValidationStatus.UNVERIFIED
    validation_message: str | None = None
    last_validated_at: datetime | None = None

    if config_in.sync_models_on_create:
        last_validated_at = datetime.now(timezone.utc)
        if definition.supports_model_discovery:
            try:
                discovered_models = fetch_provider_models(
                    definition,
                    base_url=base_url,
                    secret_inputs=secret_inputs,
                )
                validation_status = ProviderValidationStatus.SUCCESS
                validation_message = "模型列表同步成功"
            except Exception as exc:
                detail = f"模型列表同步失败: {sanitize_error_text(str(exc))}"
                raise HTTPException(status_code=502, detail=detail) from exc
        else:
            validation_status = ProviderValidationStatus.UNSUPPORTED
            validation_message = "当前供应商暂不支持自动拉取模型列表"
    elif config_in.validate_on_create:
        last_validated_at = datetime.now(timezone.utc)
        validation_status, validation_message = validate_provider_connection_inputs(
            definition,
            base_url=base_url,
            secret_inputs=secret_inputs,
        )

    config = LlmProviderConfig(
        namespace_id=namespace_id,
        config_name=config_in.config_name,
        provider_slug=definition.provider_slug,
        provider_display_name=definition.display_name,
        auth_type=definition.auth_type,
        base_url=base_url,
        secret_ciphertext=seal_secret_payload(secret_inputs),
        secret_masked=get_primary_secret_mask(definition, secret_inputs),
        extra_config=config_in.extra_config,
        supports_health_check=definition.supports_health_check,
        supports_model_discovery=definition.supports_model_discovery,
        validation_status=validation_status,
        validation_message=validation_message,
        last_validated_at=last_validated_at,
        enabled=config_in.enabled,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    config = crud.create_llm_provider_config(session=session, config=config)

    merged_models = merge_provider_models(
        [],
        discovered_models=discovered_models,
        manual_models=config_in.manual_models,
        enabled_model_ids=config_in.enabled_model_ids,
    )
    crud.replace_llm_provider_models(
        session=session,
        config=config,
        models_payload=merged_models,
    )
    config = _require_namespace_config(
        session=session, config_id=config.id, namespace_id=namespace_id
    )
    enqueue_platform_reconcile_job(session, namespace_id, trigger="provider_created")
    reconcile_platform_model_bindings(session, namespace_id)
    session.commit()
    return to_provider_config_public(config)


@router.post("/provider-configs/draft/validate", response_model=LlmProviderDraftResult)
def validate_draft_provider_config(
    draft_in: LlmProviderDraftRequest,
    _: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    definition, base_url, secret_inputs = _prepare_draft_provider(draft_in)
    status, message = validate_provider_connection_inputs(
        definition,
        base_url=base_url,
        secret_inputs=secret_inputs,
    )
    return LlmProviderDraftResult(
        validation_status=status,
        validation_message=message,
        models=[],
    )


@router.post(
    "/provider-configs/draft/sync-models", response_model=LlmProviderDraftResult
)
def sync_draft_provider_models(
    draft_in: LlmProviderDraftRequest,
    _: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    definition, base_url, secret_inputs = _prepare_draft_provider(draft_in)
    discovered_models: list[dict[str, Any]] | None = None

    if definition.supports_model_discovery:
        try:
            discovered_models = fetch_provider_models(
                definition,
                base_url=base_url,
                secret_inputs=secret_inputs,
            )
            validation_status = ProviderValidationStatus.SUCCESS
            validation_message = "模型列表同步成功"
        except Exception as exc:
            validation_status = ProviderValidationStatus.FAILED
            validation_message = f"模型列表同步失败: {sanitize_error_text(str(exc))}"
    else:
        validation_status = ProviderValidationStatus.UNSUPPORTED
        validation_message = "当前供应商暂不支持自动拉取模型列表"

    merged_models = merge_provider_models(
        [],
        discovered_models=discovered_models,
        manual_models=draft_in.manual_models,
        enabled_model_ids=draft_in.enabled_model_ids,
    )
    return LlmProviderDraftResult(
        validation_status=validation_status,
        validation_message=validation_message,
        models=[LlmProviderModelPreview.model_validate(item) for item in merged_models],
    )


@router.patch("/provider-configs/{config_id}", response_model=LlmProviderConfigPublic)
def update_provider_config(
    config_id: uuid.UUID,
    *,
    session: SessionDep,
    current_user: CurrentUser,
    config_in: LlmProviderConfigUpdate,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    config = _require_namespace_config(
        session=session, config_id=config_id, namespace_id=namespace_id
    )
    definition = get_provider_definition(config.provider_slug)
    route_credentials_changed = False

    if config_in.config_name is not None:
        config.config_name = config_in.config_name
    if config_in.base_url is not None:
        try:
            base_url = canonical_endpoint(config_in.base_url)
        except EndpointValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if base_url != config.base_url:
            if config_in.secret_inputs is None:
                raise HTTPException(
                    status_code=400,
                    detail="Changing base_url requires resubmitting credentials",
                )
            config.base_url = base_url
            route_credentials_changed = True
    if config_in.enabled is not None:
        config.enabled = config_in.enabled
    if config_in.extra_config is not None:
        config.extra_config = config_in.extra_config
    if config_in.secret_inputs is not None:
        try:
            secret_inputs = validate_secret_inputs(definition, config_in.secret_inputs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        config.secret_ciphertext = seal_secret_payload(secret_inputs)
        config.secret_masked = get_primary_secret_mask(definition, secret_inputs)
        route_credentials_changed = True

    if route_credentials_changed:
        config.validation_status = ProviderValidationStatus.UNVERIFIED
        config.validation_message = None
        config.last_validated_at = None
        config.enabled = False

    config.updated_by = current_user.id
    config.updated_at = datetime.now(timezone.utc)
    crud.update_llm_provider_config(session=session, config=config)

    if config_in.manual_models is not None or config_in.enabled_model_ids is not None:
        merged_models = merge_provider_models(
            config.models,
            discovered_models=None,
            manual_models=config_in.manual_models or [],
            enabled_model_ids=config_in.enabled_model_ids or [],
        )
        crud.replace_llm_provider_models(
            session=session,
            config=config,
            models_payload=merged_models,
        )
    config = _require_namespace_config(
        session=session, config_id=config_id, namespace_id=namespace_id
    )

    reconcile_platform_model_bindings(session, namespace_id)
    enqueue_platform_reconcile_job(session, namespace_id, trigger="provider_updated")
    session.commit()

    return to_provider_config_public(config)


@router.post(
    "/provider-configs/{config_id}/validate", response_model=LlmProviderConfigPublic
)
def validate_provider_config(
    config_id: uuid.UUID,
    *,
    session: SessionDep,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    config = _require_namespace_config(
        session=session, config_id=config_id, namespace_id=namespace_id
    )
    status, message = validate_provider_connection(config)
    crud.set_llm_provider_validation(
        session=session,
        config=config,
        status=status,
        message=message,
    )
    config = _require_namespace_config(
        session=session, config_id=config_id, namespace_id=namespace_id
    )
    enqueue_platform_reconcile_job(session, namespace_id, trigger="provider_validated")
    reconcile_platform_model_bindings(session, namespace_id)
    session.commit()
    return to_provider_config_public(config)


@router.get(
    "/provider-configs/{config_id}/runtime-readiness",
    response_model=ProviderRuntimeReadiness,
)
def read_provider_runtime_readiness(
    config_id: uuid.UUID,
    *,
    session: SessionDep,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> ProviderRuntimeReadiness:
    config = _require_namespace_config(
        session=session, config_id=config_id, namespace_id=namespace_id
    )
    runtime = session.exec(
        select(RuntimeInstance).where(
            RuntimeInstance.namespace_id == namespace_id,
            RuntimeInstance.management_type == RuntimeManagementType.PLATFORM_BUILTIN,
        )
    ).first()
    capability = (
        session.get(RuntimeCapabilityReport, runtime.current_capability_report_id)
        if runtime is not None and runtime.current_capability_report_id is not None
        else None
    )
    reported_release_digest = (
        capability.capabilities.get("release_digest")
        if capability is not None
        else None
    )
    if not isinstance(reported_release_digest, str):
        reported_release_digest = None
    release_trusted = bool(
        settings.RUNTIME_WORKER_RELEASE_DIGEST is not None
        and reported_release_digest == settings.RUNTIME_WORKER_RELEASE_DIGEST
    )
    job = session.exec(
        select(PlatformRuntimeReconcileJob)
        .where(PlatformRuntimeReconcileJob.namespace_id == namespace_id)
        .order_by(col(PlatformRuntimeReconcileJob.updated_at).desc())
    ).first()

    model_rows: list[ProviderModelRuntimeReadiness] = []
    for model in config.models:
        validation = session.exec(
            select(LlmProviderModelValidation)
            .where(LlmProviderModelValidation.provider_model_id == model.id)
            .order_by(col(LlmProviderModelValidation.started_at).desc())
        ).first()
        binding = (
            session.exec(
                select(RuntimeModelBinding).where(
                    RuntimeModelBinding.runtime_instance_id == runtime.id,
                    RuntimeModelBinding.provider_config_id == config.id,
                    RuntimeModelBinding.provider_model_id == model.id,
                )
            ).first()
            if runtime is not None
            else None
        )
        ready = bool(
            model.is_enabled
            and binding is not None
            and binding.status == RuntimeModelBindingStatus.AVAILABLE
        )
        model_rows.append(
            ProviderModelRuntimeReadiness(
                provider_model_id=model.id,
                model_id=model.model_id,
                enabled=model.is_enabled,
                model_definition_id=model.model_definition_id,
                validation_status=(validation.status if validation else "not_started"),
                validation_error=validation.error if validation else None,
                validation_completed_at=(
                    validation.completed_at if validation else None
                ),
                validation_valid_until=validation.valid_until if validation else None,
                binding_id=binding.id if binding else None,
                binding_status=binding.status.value if binding else None,
                binding_error=binding.last_error if binding else None,
                ready=ready,
            )
        )

    enabled_models = [row for row in model_rows if row.enabled]
    if job is not None and job.status.value in {"queued", "running"}:
        status = "reconciling"
    elif not enabled_models:
        status = "not_configured"
    elif all(row.ready for row in enabled_models):
        status = "ready"
    else:
        status = "blocked"
    return ProviderRuntimeReadiness(
        status=status,
        expected_worker_release_digest=settings.RUNTIME_WORKER_RELEASE_DIGEST,
        reported_worker_release_digest=reported_release_digest,
        release_trusted=release_trusted,
        runtime_instance_id=runtime.id if runtime else None,
        runtime_status=runtime.status.value if runtime else None,
        reconcile_job_id=job.id if job else None,
        reconcile_status=job.status.value if job else None,
        reconcile_trigger=job.trigger if job else None,
        reconcile_attempt_count=job.attempt_count if job else 0,
        reconcile_error=job.last_error if job else None,
        models=model_rows,
    )


@router.post(
    "/provider-configs/{config_id}/sync-models", response_model=LlmProviderConfigPublic
)
def sync_provider_models(
    config_id: uuid.UUID,
    *,
    session: SessionDep,
    sync_in: LlmProviderSyncModelsRequest,
    namespace_id: uuid.UUID = Depends(require_namespace_admin),
) -> Any:
    config = _require_namespace_config(
        session=session, config_id=config_id, namespace_id=namespace_id
    )
    definition = get_provider_definition(config.provider_slug)
    discovered_models: list[dict[str, Any]] | None = None

    if definition.supports_model_discovery:
        secret_inputs = open_secret_payload(config.secret_ciphertext)
        try:
            discovered_models = fetch_provider_models(
                definition,
                base_url=config.base_url,
                secret_inputs=secret_inputs,
            )
            validation_status = ProviderValidationStatus.SUCCESS
            validation_message = "模型列表同步成功"
        except Exception as exc:
            validation_status = ProviderValidationStatus.FAILED
            validation_message = f"模型列表同步失败: {exc}"
            discovered_models = None
    else:
        validation_status = ProviderValidationStatus.UNSUPPORTED
        validation_message = "当前供应商暂不支持自动拉取模型列表"

    merged_models = merge_provider_models(
        config.models,
        discovered_models=discovered_models,
        manual_models=sync_in.manual_models,
        enabled_model_ids=sync_in.enabled_model_ids,
    )
    crud.replace_llm_provider_models(
        session=session,
        config=config,
        models_payload=merged_models,
    )
    crud.set_llm_provider_validation(
        session=session,
        config=config,
        status=validation_status,
        message=validation_message,
    )
    config = _require_namespace_config(
        session=session, config_id=config_id, namespace_id=namespace_id
    )
    enqueue_platform_reconcile_job(
        session, namespace_id, trigger="provider_models_synced"
    )
    reconcile_platform_model_bindings(session, namespace_id)
    session.commit()
    return to_provider_config_public(config)
