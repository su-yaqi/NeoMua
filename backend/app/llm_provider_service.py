import base64
import hashlib
import hmac
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.models import (
    LlmProviderCatalogField,
    LlmProviderCatalogItem,
    LlmProviderConfig,
    LlmProviderConfigPublic,
    LlmProviderModel,
    LlmProviderModelInput,
    LlmProviderModelPublic,
    ProviderAuthType,
    ProviderModelSourceType,
    ProviderModelSyncStatus,
    ProviderValidationStatus,
)


@dataclass(frozen=True)
class ProviderDefinition:
    provider_slug: str
    display_name: str
    auth_type: ProviderAuthType
    default_base_url: str | None
    description: str = ""
    supports_health_check: bool = False
    supports_model_discovery: bool = False
    base_url_editable: bool = False
    secret_fields: list[LlmProviderCatalogField] = field(default_factory=list)
    extra_fields: list[LlmProviderCatalogField] = field(default_factory=list)
    models_url: str | None = None
    auth_header_kind: str = "bearer"
    extra_headers: dict[str, str] = field(default_factory=dict)


def _secret_field(
    name: str,
    label: str,
    *,
    required: bool = True,
    placeholder: str | None = None,
    help_text: str | None = None,
) -> LlmProviderCatalogField:
    return LlmProviderCatalogField(
        name=name,
        label=label,
        required=required,
        placeholder=placeholder,
        help_text=help_text,
    )


def _extra_field(
    name: str,
    label: str,
    *,
    required: bool = True,
    placeholder: str | None = None,
    help_text: str | None = None,
) -> LlmProviderCatalogField:
    return LlmProviderCatalogField(
        name=name,
        label=label,
        required=required,
        placeholder=placeholder,
        help_text=help_text,
    )


PROVIDER_DEFINITIONS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition(
        "alibaba",
        "Alibaba Cloud / DashScope",
        ProviderAuthType.API_KEY,
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "alibaba-coding-plan",
        "Alibaba Cloud (Coding Plan)",
        ProviderAuthType.API_KEY,
        "https://coding-intl.dashscope.aliyuncs.com/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "anthropic",
        "Anthropic",
        ProviderAuthType.API_KEY,
        "https://api.anthropic.com",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Key")],
        models_url="https://api.anthropic.com/v1/models",
        auth_header_kind="x-api-key",
        extra_headers={"anthropic-version": "2023-06-01"},
    ),
    ProviderDefinition(
        "arcee",
        "Arcee AI",
        ProviderAuthType.API_KEY,
        "https://api.arcee.ai/api/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "azure-foundry",
        "Azure Foundry",
        ProviderAuthType.API_KEY,
        "",
        description="Azure AI Foundry custom endpoint",
        supports_health_check=True,
        supports_model_discovery=True,
        base_url_editable=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "bedrock",
        "AWS Bedrock",
        ProviderAuthType.AWS_SDK,
        "https://bedrock-runtime.us-east-1.amazonaws.com",
        base_url_editable=True,
        secret_fields=[
            _secret_field("aws_access_key_id", "AWS Access Key ID"),
            _secret_field("aws_secret_access_key", "AWS Secret Access Key"),
            _secret_field("aws_session_token", "AWS Session Token", required=False),
        ],
        extra_fields=[_extra_field("region", "AWS Region", placeholder="us-east-1")],
    ),
    ProviderDefinition(
        "copilot",
        "GitHub Copilot / GitHub Models",
        ProviderAuthType.COPILOT_TOKEN,
        "https://api.githubcopilot.com",
        secret_fields=[_secret_field("api_token", "GitHub Token")],
    ),
    ProviderDefinition(
        "copilot-acp",
        "GitHub Copilot ACP",
        ProviderAuthType.EXTERNAL_PROCESS,
        "acp://copilot",
    ),
    ProviderDefinition(
        "custom",
        "Custom / Local OpenAI-compatible",
        ProviderAuthType.CUSTOM,
        "",
        description="任意 OpenAI-compatible endpoint",
        supports_health_check=True,
        supports_model_discovery=True,
        base_url_editable=True,
        secret_fields=[_secret_field("api_token", "API Token", required=False)],
    ),
    ProviderDefinition(
        "deepseek",
        "DeepSeek",
        ProviderAuthType.API_KEY,
        "https://api.deepseek.com/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "gemini",
        "Gemini",
        ProviderAuthType.API_KEY,
        "https://generativelanguage.googleapis.com/v1beta",
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "google-gemini-cli",
        "Gemini CLI / Cloud Code",
        ProviderAuthType.OAUTH_EXTERNAL,
        "cloudcode-pa://google",
        secret_fields=[
            _secret_field("access_token", "Access Token", required=False),
            _secret_field("refresh_token", "Refresh Token", required=False),
        ],
    ),
    ProviderDefinition(
        "gmi",
        "GMI Cloud",
        ProviderAuthType.API_KEY,
        "https://api.gmi-serving.com/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "huggingface",
        "Hugging Face",
        ProviderAuthType.API_KEY,
        "https://router.huggingface.co/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "HF Token")],
    ),
    ProviderDefinition(
        "kilocode",
        "Kilo Code",
        ProviderAuthType.API_KEY,
        "https://api.kilo.ai/api/gateway",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
        models_url="https://api.kilo.ai/api/gateway/models",
    ),
    ProviderDefinition(
        "kimi-coding",
        "Kimi / Moonshot",
        ProviderAuthType.API_KEY,
        "https://api.moonshot.ai/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "kimi-coding-cn",
        "Kimi / Moonshot (China)",
        ProviderAuthType.API_KEY,
        "https://api.moonshot.cn/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "minimax",
        "MiniMax",
        ProviderAuthType.API_KEY,
        "https://api.minimax.io/anthropic",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
        models_url="https://api.minimax.io/v1/models",
    ),
    ProviderDefinition(
        "minimax-cn",
        "MiniMax (China)",
        ProviderAuthType.API_KEY,
        "https://api.minimaxi.com/anthropic",
        secret_fields=[_secret_field("api_token", "API Token")],
        models_url="https://api.minimaxi.com/v1/models",
    ),
    ProviderDefinition(
        "minimax-oauth",
        "MiniMax (OAuth)",
        ProviderAuthType.OAUTH_EXTERNAL,
        "https://api.minimax.io/anthropic",
        secret_fields=[
            _secret_field("access_token", "Access Token", required=False),
            _secret_field("refresh_token", "Refresh Token", required=False),
        ],
    ),
    ProviderDefinition(
        "nous",
        "Nous Research",
        ProviderAuthType.OAUTH_DEVICE_CODE,
        "https://inference.nousresearch.com/v1",
        secret_fields=[_secret_field("access_token", "Access Token", required=False)],
    ),
    ProviderDefinition(
        "novita",
        "NovitaAI",
        ProviderAuthType.API_KEY,
        "https://api.novita.ai/openai/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "nvidia",
        "NVIDIA NIM",
        ProviderAuthType.API_KEY,
        "https://integrate.api.nvidia.com/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "ollama-cloud",
        "Ollama Cloud",
        ProviderAuthType.API_KEY,
        "https://ollama.com/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "openai-codex",
        "OpenAI Codex",
        ProviderAuthType.OAUTH_EXTERNAL,
        "https://chatgpt.com/backend-api/codex",
        secret_fields=[_secret_field("access_token", "Access Token", required=False)],
    ),
    ProviderDefinition(
        "opencode-zen",
        "OpenCode Zen",
        ProviderAuthType.API_KEY,
        "https://opencode.ai/zen/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "opencode-go",
        "OpenCode Go",
        ProviderAuthType.API_KEY,
        "https://opencode.ai/zen/go/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "openrouter",
        "OpenRouter",
        ProviderAuthType.API_KEY,
        "https://openrouter.ai/api/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
        models_url="https://openrouter.ai/api/v1/models",
        auth_header_kind="none",
    ),
    ProviderDefinition(
        "qwen-oauth",
        "Qwen Portal",
        ProviderAuthType.OAUTH_EXTERNAL,
        "https://portal.qwen.ai/v1",
        secret_fields=[_secret_field("access_token", "Access Token", required=False)],
    ),
    ProviderDefinition(
        "stepfun",
        "StepFun",
        ProviderAuthType.API_KEY,
        "https://api.stepfun.ai/step_plan/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "xai",
        "xAI / Grok",
        ProviderAuthType.API_KEY,
        "https://api.x.ai/v1",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "xiaomi",
        "Xiaomi MiMo",
        ProviderAuthType.API_KEY,
        "https://api.xiaomimimo.com/v1",
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
    ProviderDefinition(
        "zai",
        "Z.AI (GLM)",
        ProviderAuthType.API_KEY,
        "https://api.z.ai/api/paas/v4",
        supports_health_check=True,
        supports_model_discovery=True,
        secret_fields=[_secret_field("api_token", "API Token")],
    ),
)

PROVIDER_DEFINITION_MAP = {item.provider_slug: item for item in PROVIDER_DEFINITIONS}


def list_provider_catalog_items() -> list[LlmProviderCatalogItem]:
    return [
        LlmProviderCatalogItem(
            provider_slug=item.provider_slug,
            display_name=item.display_name,
            description=item.description or None,
            auth_type=item.auth_type,
            default_base_url=item.default_base_url or None,
            supports_health_check=item.supports_health_check,
            supports_model_discovery=item.supports_model_discovery,
            base_url_editable=item.base_url_editable,
            secret_fields=item.secret_fields,
            extra_fields=item.extra_fields,
        )
        for item in PROVIDER_DEFINITIONS
    ]


def get_provider_definition(provider_slug: str) -> ProviderDefinition:
    item = PROVIDER_DEFINITION_MAP.get(provider_slug)
    if item is None:
        raise ValueError(f"Unknown provider: {provider_slug}")
    return item


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _secret_key_bytes() -> bytes:
    return hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()


def _derive_keystream(length: int, nonce: bytes) -> bytes:
    blocks: list[bytes] = []
    counter = 0
    key = _secret_key_bytes()
    while sum(len(block) for block in blocks) < length:
        blocks.append(
            hashlib.blake2b(
                nonce + counter.to_bytes(4, "big"),
                key=key,
                digest_size=32,
            ).digest()
        )
        counter += 1
    return b"".join(blocks)[:length]


def seal_secret_payload(payload: dict[str, str]) -> str | None:
    if not payload:
        return None
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    nonce = os.urandom(16)
    keystream = _derive_keystream(len(raw), nonce)
    ciphertext = bytes(left ^ right for left, right in zip(raw, keystream, strict=True))
    signature = hmac.new(
        _secret_key_bytes(), nonce + ciphertext, hashlib.sha256
    ).digest()
    return ".".join(
        ["v1", _b64_encode(nonce), _b64_encode(ciphertext), _b64_encode(signature)]
    )


def open_secret_payload(ciphertext: str | None) -> dict[str, str]:
    if not ciphertext:
        return {}
    version, encoded_nonce, encoded_ciphertext, encoded_signature = ciphertext.split(
        "."
    )
    if version != "v1":
        raise ValueError("Unsupported secret payload version")
    nonce = _b64_decode(encoded_nonce)
    encrypted = _b64_decode(encoded_ciphertext)
    expected_signature = _b64_decode(encoded_signature)
    actual_signature = hmac.new(
        _secret_key_bytes(), nonce + encrypted, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected_signature, actual_signature):
        raise ValueError("Secret payload signature mismatch")
    keystream = _derive_keystream(len(encrypted), nonce)
    raw = bytes(left ^ right for left, right in zip(encrypted, keystream, strict=True))
    return json.loads(raw.decode("utf-8"))


def mask_secret_value(value: str | None) -> str | None:
    if not value:
        return None
    suffix = value[-4:] if len(value) >= 4 else value
    if len(value) <= 4:
        return "*" * len(value)
    prefix = value[:3]
    return f"{prefix}***{suffix}"


def get_primary_secret_mask(
    definition: ProviderDefinition, secret_inputs: dict[str, str]
) -> str | None:
    for secret_field in definition.secret_fields:
        value = secret_inputs.get(secret_field.name)
        if value:
            return mask_secret_value(value)
    for value in secret_inputs.values():
        if value:
            return mask_secret_value(value)
    return None


def validate_secret_inputs(
    definition: ProviderDefinition, secret_inputs: dict[str, str] | None
) -> dict[str, str]:
    normalized = {key: value for key, value in (secret_inputs or {}).items() if value}
    if definition.auth_type == ProviderAuthType.CUSTOM:
        return normalized
    if definition.auth_type in {
        ProviderAuthType.OAUTH_EXTERNAL,
        ProviderAuthType.OAUTH_DEVICE_CODE,
        ProviderAuthType.EXTERNAL_PROCESS,
    }:
        return normalized
    required_fields = [
        field.name for field in definition.secret_fields if field.required
    ]
    if required_fields and not all(normalized.get(name) for name in required_fields):
        names = ", ".join(required_fields)
        raise ValueError(f"Missing required secret fields: {names}")
    return normalized


def _build_models_url(definition: ProviderDefinition, base_url: str) -> str | None:
    if definition.models_url:
        return definition.models_url
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/models"


def _build_request_headers(
    definition: ProviderDefinition, secret_inputs: dict[str, str]
) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if definition.auth_header_kind == "bearer":
        api_token = secret_inputs.get("api_token") or secret_inputs.get("access_token")
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
    elif definition.auth_header_kind == "x-api-key":
        api_token = secret_inputs.get("api_token") or secret_inputs.get("access_token")
        if api_token:
            headers["x-api-key"] = api_token
    headers.update(definition.extra_headers)
    return headers


def parse_model_ids(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("data", [])
    else:
        items = []
    models: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not model_id:
            continue
        models.append(item)
    return models


def fetch_provider_models(
    definition: ProviderDefinition,
    *,
    base_url: str,
    secret_inputs: dict[str, str],
) -> list[dict[str, Any]]:
    models_url = _build_models_url(definition, base_url)
    if not models_url:
        raise ValueError("当前供应商未配置可探测的模型目录地址")
    headers = _build_request_headers(definition, secret_inputs)
    response = httpx.get(models_url, headers=headers, timeout=8)
    response.raise_for_status()
    return parse_model_ids(response.json())


def validate_provider_connection(
    config: LlmProviderConfig,
) -> tuple[ProviderValidationStatus, str]:
    definition = get_provider_definition(config.provider_slug)
    if not definition.supports_health_check:
        return ProviderValidationStatus.UNSUPPORTED, "当前供应商暂不支持在线校验"
    secret_inputs = open_secret_payload(config.secret_ciphertext)
    try:
        fetch_provider_models(
            definition,
            base_url=config.base_url,
            secret_inputs=secret_inputs,
        )
    except Exception as exc:
        return (
            ProviderValidationStatus.FAILED,
            f"连接校验失败: {sanitize_error_text(str(exc))}",
        )
    return ProviderValidationStatus.SUCCESS, "连接校验成功"


def sanitize_error_text(text: str) -> str:
    sanitized = text
    for marker in ("Bearer ", "sk-", "ghp_", "glpat-"):
        if marker in sanitized:
            sanitized = sanitized.replace(marker, "***")
    return sanitized


def merge_provider_models(
    existing_models: Iterable[LlmProviderModel],
    *,
    discovered_models: list[dict[str, Any]] | None,
    manual_models: list[LlmProviderModelInput],
    enabled_model_ids: list[str],
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    existing_by_id = {model.model_id: model for model in existing_models}
    merged: dict[str, dict[str, Any]] = {}

    if discovered_models is None:
        for model in existing_models:
            if model.source_type == ProviderModelSourceType.DISCOVERED:
                merged[model.model_id] = {
                    "model_id": model.model_id,
                    "display_name": model.display_name or model.model_id,
                    "source_type": ProviderModelSourceType.DISCOVERED,
                    "sync_status": model.sync_status,
                    "raw_metadata": model.raw_metadata,
                    "last_synced_at": model.last_synced_at,
                }
    else:
        discovered_ids = set()
        for item in discovered_models:
            model_id = str(item["id"])
            discovered_ids.add(model_id)
            merged[model_id] = {
                "model_id": model_id,
                "display_name": item.get("name")
                or item.get("display_name")
                or model_id,
                "source_type": ProviderModelSourceType.DISCOVERED,
                "sync_status": ProviderModelSyncStatus.ACTIVE,
                "raw_metadata": item,
                "last_synced_at": now,
            }
        for model in existing_models:
            if (
                model.source_type == ProviderModelSourceType.DISCOVERED
                and model.model_id not in discovered_ids
            ):
                merged[model.model_id] = {
                    "model_id": model.model_id,
                    "display_name": model.display_name or model.model_id,
                    "source_type": ProviderModelSourceType.DISCOVERED,
                    "sync_status": ProviderModelSyncStatus.STALE,
                    "raw_metadata": model.raw_metadata,
                    "last_synced_at": model.last_synced_at,
                }

    for item in manual_models:
        merged[item.model_id] = {
            "model_id": item.model_id,
            "display_name": item.display_name or item.model_id,
            "source_type": ProviderModelSourceType.MANUAL,
            "sync_status": ProviderModelSyncStatus.ACTIVE,
            "raw_metadata": {},
            "last_synced_at": now,
        }

    enabled_set = set(enabled_model_ids)
    results: list[dict[str, Any]] = []
    for model_id, payload in sorted(merged.items(), key=lambda item: item[0]):
        results.append(
            {
                "id": existing_by_id.get(model_id).id
                if model_id in existing_by_id
                else None,
                **payload,
                "is_enabled": model_id in enabled_set,
                "created_at": existing_by_id.get(model_id).created_at
                if model_id in existing_by_id
                else now,
                "updated_at": now,
            }
        )
    return results


def to_provider_model_public(model: LlmProviderModel) -> LlmProviderModelPublic:
    return LlmProviderModelPublic(
        id=model.id,
        model_id=model.model_id,
        display_name=model.display_name,
        source_type=model.source_type,
        is_enabled=model.is_enabled,
        sync_status=model.sync_status,
        raw_metadata=model.raw_metadata,
        last_synced_at=model.last_synced_at,
    )


def to_provider_config_public(config: LlmProviderConfig) -> LlmProviderConfigPublic:
    models = sorted(config.models, key=lambda item: item.model_id)
    return LlmProviderConfigPublic(
        id=config.id,
        namespace_id=config.namespace_id,
        config_name=config.config_name,
        provider_slug=config.provider_slug,
        provider_display_name=config.provider_display_name,
        auth_type=config.auth_type,
        base_url=config.base_url,
        secret_masked=config.secret_masked,
        extra_config=config.extra_config,
        supports_health_check=config.supports_health_check,
        supports_model_discovery=config.supports_model_discovery,
        validation_status=config.validation_status,
        validation_message=config.validation_message,
        last_validated_at=config.last_validated_at,
        enabled=config.enabled,
        created_at=config.created_at,
        updated_at=config.updated_at,
        models=[to_provider_model_public(model) for model in models],
    )
