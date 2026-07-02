import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app import crud
from app.api.deps import CurrentUser, SessionDep, require_namespace_admin
from app.llm_provider_service import (
    fetch_provider_models,
    get_primary_secret_mask,
    get_provider_definition,
    list_provider_catalog_items,
    merge_provider_models,
    open_secret_payload,
    seal_secret_payload,
    to_provider_config_public,
    validate_provider_connection,
    validate_secret_inputs,
)
from app.models import (
    LlmProviderCatalogPublic,
    LlmProviderConfig,
    LlmProviderConfigCreate,
    LlmProviderConfigPublic,
    LlmProviderConfigsPublic,
    LlmProviderConfigUpdate,
    LlmProviderSyncModelsRequest,
    ProviderValidationStatus,
)

router = APIRouter(prefix="/llm", tags=["llm"])


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

    config = LlmProviderConfig(
        namespace_id=namespace_id,
        config_name=config_in.config_name,
        provider_slug=definition.provider_slug,
        provider_display_name=definition.display_name,
        auth_type=definition.auth_type,
        base_url=config_in.base_url,
        secret_ciphertext=seal_secret_payload(secret_inputs),
        secret_masked=get_primary_secret_mask(definition, secret_inputs),
        extra_config=config_in.extra_config,
        supports_health_check=definition.supports_health_check,
        supports_model_discovery=definition.supports_model_discovery,
        validation_status=ProviderValidationStatus.UNVERIFIED,
        enabled=config_in.enabled,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    config = crud.create_llm_provider_config(session=session, config=config)

    merged_models = merge_provider_models(
        [],
        discovered_models=None,
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
    return to_provider_config_public(config)


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

    if config_in.config_name is not None:
        config.config_name = config_in.config_name
    if config_in.base_url is not None:
        config.base_url = config_in.base_url
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
    return to_provider_config_public(config)


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
    return to_provider_config_public(config)
