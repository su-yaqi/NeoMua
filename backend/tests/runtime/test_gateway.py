import pytest

from app.runtime.gateway import UnsupportedGatewayProvider, gateway_provider_kind


def test_gateway_provider_matrix_is_fail_closed() -> None:
    assert gateway_provider_kind("anthropic") == "anthropic"
    assert gateway_provider_kind("deepseek") == "openai_compatible"
    with pytest.raises(UnsupportedGatewayProvider):
        gateway_provider_kind("bedrock")
