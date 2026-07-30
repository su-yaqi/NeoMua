import pytest

from app.runtime.endpoints import EndpointValidationError, canonical_endpoint


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("HTTPS://Example.COM:443/v1/", "https://example.com/v1"),
        ("http://10.0.0.1:8080/", "http://10.0.0.1:8080"),
        ("http://[fd00::1]/v1", "http://[fd00::1]/v1"),
    ],
)
def test_canonical_endpoint_preserves_private_targets(
    value: str, expected: str
) -> None:
    assert canonical_endpoint(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "file:///tmp/socket",
        "https://user:password@example.com",
        "https://example.com/#fragment",
        "https://example.com/?token=value",
        "https://example.com:99999",
    ],
)
def test_canonical_endpoint_rejects_unsafe_url_forms(value: str) -> None:
    with pytest.raises(EndpointValidationError):
        canonical_endpoint(value)
