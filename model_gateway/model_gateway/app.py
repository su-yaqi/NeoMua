import hmac
import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from model_gateway.translation import UnsupportedCapability, validate_capabilities

app = FastAPI(title="NeoMua Model Gateway")


def _authorize(authorization: str | None) -> None:
    expected = os.getenv("GATEWAY_SERVICE_TOKEN")
    if not expected or not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Gateway credential required")
    if not hmac.compare_digest(authorization[7:], expected):
        raise HTTPException(403, "Invalid gateway credential")


@app.post("/v1/messages")
async def messages(
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_provider_kind: str = Header(default="anthropic", alias="X-Provider-Kind"),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        validate_capabilities(body, x_provider_kind)
    except UnsupportedCapability as exc:
        raise HTTPException(422, {"code": "unsupported_model_capability", "message": str(exc)})
    raise HTTPException(503, {"code": "upstream_not_configured", "message": "No upstream route was resolved"})


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
