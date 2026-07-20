"""Exact provider-model validation shared by automatic and explicit bindings."""

from typing import Any

import httpx

from app.llm_provider_service import open_secret_payload, sanitize_error_text
from app.models import LlmProviderConfig
from app.runtime.catalog import RuntimeCatalogError
from app.runtime.gateway import UnsupportedGatewayProvider, gateway_provider_kind


def validate_provider_model_call(
    provider: LlmProviderConfig, model_id: str
) -> dict[str, Any]:
    try:
        kind = gateway_provider_kind(provider.provider_slug)
        secrets = open_secret_payload(provider.secret_ciphertext)
        api_key = (
            secrets.get("api_key") or secrets.get("api_token") or secrets.get("token")
        )
        if not api_key:
            raise RuntimeCatalogError(
                "provider_credential_missing", "Provider credential is missing"
            )
        base_url = provider.base_url.rstrip("/")
        if kind == "anthropic":
            url = (
                f"{base_url}/messages"
                if base_url.endswith("/v1")
                else f"{base_url}/v1/messages"
            )
            response = httpx.post(
                url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model_id,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                },
                timeout=30,
            )
        else:
            url = (
                f"{base_url}/chat/completions"
                if base_url.endswith("/v1")
                else f"{base_url}/v1/chat/completions"
            )
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model_id,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "Reply OK"}],
                },
                timeout=30,
            )
        response.raise_for_status()
        return {
            "provider_kind": kind,
            "status_code": response.status_code,
            "model_id": model_id,
        }
    except UnsupportedGatewayProvider as exc:
        raise RuntimeCatalogError("provider_adapter_unsupported", str(exc)) from exc
    except httpx.HTTPError as exc:
        raise RuntimeCatalogError(
            "provider_model_validation_failed", sanitize_error_text(str(exc))
        ) from exc
