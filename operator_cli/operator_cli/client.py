import json
import time
import uuid
from typing import Any

import httpx

from operator_cli.keychain import SystemKeychain
from operator_cli.profiles import Profile, ProfileStore


class ApiError(RuntimeError):
    def __init__(self, response: httpx.Response):
        self.status_code = response.status_code
        try:
            detail = response.json()
        except ValueError:
            detail = {"detail": response.text[:1000]}
        super().__init__(json.dumps(detail, ensure_ascii=False))


class ApiClient:
    def __init__(
        self, profile: Profile, profiles: ProfileStore, keychain: SystemKeychain
    ) -> None:
        self.profile = profile
        self.profiles = profiles
        self.keychain = keychain
        self.client = httpx.Client(
            base_url=profile.api_url, timeout=30, follow_redirects=False
        )
        self.access_token: str | None = None
        self.access_expires_at = 0.0

    def close(self) -> None:
        self.client.close()

    def _refresh(self) -> None:
        refresh = self.keychain.get(self.profile.credential_id)
        response = self.client.post(
            "/api/v1/cli/refresh", json={"refresh_token": refresh}
        )
        if response.status_code != 200:
            raise ApiError(response)
        payload = response.json()
        self.keychain.set(self.profile.credential_id, payload["refresh_token"])
        self.access_token = payload["access_token"]
        self.access_expires_at = (
            time.monotonic() + int(payload.get("expires_in", 900)) - 30
        )

    def _headers(self) -> dict[str, str]:
        if not self.access_token or time.monotonic() >= self.access_expires_at:
            self._refresh()
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if self.profile.namespace_id:
            headers["X-Namespace-Id"] = self.profile.namespace_id
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data_body: Any = None,
        files: Any = None,
        idempotent_mutation: bool = False,
    ) -> Any:
        headers = self._headers()
        if method != "GET" and not path.startswith("/cli/"):
            identity = self.client.get("/api/v1/users/me", headers=headers)
            if identity.status_code >= 400:
                raise ApiError(identity)
        if idempotent_mutation:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        retries = 3 if method == "GET" or idempotent_mutation else 1
        file_positions: list[tuple[Any, int]] = []
        if isinstance(files, dict):
            for item in files.values():
                stream = item[1] if isinstance(item, tuple) and len(item) > 1 else item
                if hasattr(stream, "tell") and hasattr(stream, "seek"):
                    file_positions.append((stream, stream.tell()))
        response: httpx.Response | None = None
        for attempt in range(retries):
            for stream, position in file_positions:
                stream.seek(position)
            try:
                response = self.client.request(
                    method,
                    f"/api/v1{path}",
                    headers=headers,
                    json=json_body,
                    data=data_body,
                    files=files,
                )
            except httpx.TransportError:
                if attempt + 1 == retries:
                    raise
                time.sleep(0.25 * (2**attempt))
                continue
            if (
                response.status_code not in {408, 425, 429, 502, 503, 504}
                or attempt + 1 == retries
            ):
                break
            time.sleep(0.25 * (2**attempt))
        assert response is not None
        if response.status_code >= 400:
            raise ApiError(response)
        return None if response.status_code == 204 else response.json()

    def verify_protocol(self) -> None:
        capabilities = self.request("GET", "/capabilities")
        if capabilities.get("cli_protocol_version") != "1.0":
            raise RuntimeError("Server CLI protocol is incompatible; mutation stopped")
