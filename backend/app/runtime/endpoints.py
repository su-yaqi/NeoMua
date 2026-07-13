import ipaddress
from urllib.parse import SplitResult, urlsplit, urlunsplit


class EndpointValidationError(ValueError):
    pass


def canonical_endpoint(value: str) -> str:
    """Normalize a provider endpoint without excluding private network targets."""
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise EndpointValidationError("endpoint has an invalid host or port") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise EndpointValidationError("endpoint scheme must be http or https")
    if not parsed.hostname:
        raise EndpointValidationError("endpoint host is required")
    if parsed.username is not None or parsed.password is not None:
        raise EndpointValidationError("endpoint userinfo is not allowed")
    if parsed.fragment:
        raise EndpointValidationError("endpoint fragment is not allowed")
    if parsed.query:
        raise EndpointValidationError("endpoint query is not allowed")

    hostname = parsed.hostname.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(hostname)
        host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    except ValueError:
        try:
            host = hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise EndpointValidationError("endpoint host is invalid") from exc
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit(SplitResult(parsed.scheme.lower(), netloc, path, "", ""))
