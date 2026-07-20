import hmac

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.core.config import settings


class CookieCSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        csrf_exempt = {
            f"{settings.API_V1_STR}/login/access-token",
            f"{settings.API_V1_STR}/node/enroll",
            f"{settings.API_V1_STR}/node/bootstrap/preflight",
            f"{settings.API_V1_STR}/node/bootstrap/recover",
            f"{settings.API_V1_STR}/node/bootstrap/receipt",
            f"{settings.API_V1_STR}/node/bootstrap/stage",
            f"{settings.API_V1_STR}/users/signup",
        }
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.url.path not in csrf_exempt
            and not request.url.path.startswith(
                f"{settings.API_V1_STR}/password-recovery"
            )
            and request.url.path != f"{settings.API_V1_STR}/reset-password/"
        ):
            has_cookie_auth = bool(
                request.cookies.get("neomua_access")
                or request.cookies.get("neomua_refresh")
            )
            has_bearer = request.headers.get("authorization", "").startswith("Bearer ")
            has_internal_service_auth = bool(request.headers.get("x-runtime-token"))
            if has_cookie_auth and not has_bearer and not has_internal_service_auth:
                origin = request.headers.get("origin")
                csrf_cookie = request.cookies.get("neomua_csrf")
                csrf_header = request.headers.get("x-csrf-token")
                if (
                    origin not in settings.all_cors_origins
                    or not csrf_cookie
                    or not csrf_header
                    or not hmac.compare_digest(csrf_cookie, csrf_header)
                ):
                    return JSONResponse(
                        {"detail": "CSRF validation failed"}, status_code=403
                    )
        return await call_next(request)
