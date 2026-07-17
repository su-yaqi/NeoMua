import asyncio
import ipaddress
import json
import os
import socket
from typing import Any, cast
from urllib.parse import urljoin, urlparse

import httpx


class McpValidationError(RuntimeError):
    pass


def _streamable_response(
    response: httpx.Response, request_id: int, maximum_bytes: int
) -> dict[str, Any]:
    if len(response.content) > maximum_bytes:
        raise McpValidationError("MCP HTTP response exceeds result limit")
    content_type = (
        response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    candidates: list[str]
    if content_type == "text/event-stream":
        candidates = []
        data_lines: list[str] = []
        for line in response.text.splitlines() + [""]:
            if not line:
                if data_lines:
                    candidates.append("\n".join(data_lines))
                    data_lines = []
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
    else:
        candidates = [response.text]
    for raw in candidates:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("id") == request_id:
            return cast(dict[str, Any], payload)
    raise McpValidationError(
        "MCP HTTP response did not contain the requested JSON-RPC result"
    )


async def _assert_public_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise McpValidationError("MCP endpoint must use HTTPS")
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(
        None,
        socket.getaddrinfo,
        parsed.hostname,
        parsed.port or 443,
        socket.AF_UNSPEC,
        socket.SOCK_STREAM,
    )
    for result in results:
        address = ipaddress.ip_address(result[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise McpValidationError("MCP endpoint resolved to a forbidden address")


class McpRuntimeManager:
    def __init__(self, *, executable_registry: dict[str, str] | None = None) -> None:
        self.executable_registry = executable_registry or json.loads(
            os.environ.get("NEOMUA_MCP_EXECUTABLES", "{}")
        )

    def execution_configs(self, servers: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for server in servers:
            config = server["config"]
            secrets = server.get("secret_inputs", {})
            if server["transport"] == "stdio":
                executable = self.executable_registry.get(str(config["executable_key"]))
                if not executable or not os.path.isabs(executable):
                    raise McpValidationError(
                        f"MCP executable is unavailable: {config['executable_key']}"
                    )
                result[server["slug"]] = {
                    "type": "stdio",
                    "command": executable,
                    "args": config.get("args", []),
                    "env": secrets,
                }
            elif server["transport"] == "streamable_http":
                result[server["slug"]] = {
                    "type": "http",
                    "url": config["endpoint"],
                    "headers": secrets,
                }
            elif server["transport"] == "sse":
                result[server["slug"]] = {
                    "type": "sse",
                    "url": config["endpoint"],
                    "headers": secrets,
                }
            else:
                raise McpValidationError("Unsupported MCP transport")
        return result

    async def validate(self, command: dict[str, Any]) -> list[dict[str, Any]]:
        transport = command["transport"]
        config = command["config"]
        secret_inputs = command.get("secret_inputs", {})
        if transport == "stdio":
            return await self._validate_stdio(
                config, secret_inputs, command["protocol_version"]
            )
        if transport == "streamable_http":
            return await self._validate_http(
                config, secret_inputs, command["protocol_version"]
            )
        if transport == "sse":
            return await self._validate_sse(
                config, secret_inputs, command["protocol_version"]
            )
        raise McpValidationError("Unsupported MCP transport")

    async def _validate_sse(
        self,
        config: dict[str, Any],
        secret_inputs: dict[str, str],
        protocol_version: str,
    ) -> list[dict[str, Any]]:
        endpoint = str(config["endpoint"])
        await _assert_public_endpoint(endpoint)
        parsed_origin = urlparse(endpoint)
        headers = dict(secret_inputs)
        timeout = httpx.Timeout(
            float(config.get("call_timeout_seconds", 30)),
            connect=float(config.get("connect_timeout_seconds", 10)),
        )
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "GET", endpoint, headers={**headers, "Accept": "text/event-stream"}
            ) as stream:
                if stream.is_redirect:
                    raise McpValidationError("MCP redirect is forbidden")
                stream.raise_for_status()
                events: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

                async def read_events() -> None:
                    event_name = "message"
                    data_lines: list[str] = []
                    async for line in stream.aiter_lines():
                        if line == "":
                            data = "\n".join(data_lines)
                            if len(data.encode()) > int(
                                config.get("max_result_bytes", 10 * 1024 * 1024)
                            ):
                                raise McpValidationError(
                                    "MCP SSE event exceeds result limit"
                                )
                            await events.put((event_name, data))
                            event_name, data_lines = "message", []
                        elif line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())

                reader = asyncio.create_task(read_events())
                try:
                    event_name, data = await asyncio.wait_for(
                        events.get(),
                        timeout=float(config.get("connect_timeout_seconds", 10)),
                    )
                    if event_name != "endpoint":
                        raise McpValidationError(
                            "MCP SSE did not announce a message endpoint"
                        )
                    message_endpoint = urljoin(endpoint, data)
                    parsed_message = urlparse(message_endpoint)
                    if (
                        parsed_message.scheme != parsed_origin.scheme
                        or parsed_message.hostname != parsed_origin.hostname
                        or parsed_message.port != parsed_origin.port
                    ):
                        raise McpValidationError(
                            "MCP SSE message endpoint changed origin"
                        )
                    await _assert_public_endpoint(message_endpoint)

                    async def request(
                        request_id: int, method: str, params: dict[str, Any]
                    ) -> dict[str, Any]:
                        posted = await client.post(
                            message_endpoint,
                            headers=headers,
                            json={
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "method": method,
                                "params": params,
                            },
                        )
                        posted.raise_for_status()
                        while True:
                            name, raw = await asyncio.wait_for(
                                events.get(),
                                timeout=float(config.get("call_timeout_seconds", 30)),
                            )
                            if name != "message":
                                continue
                            payload = json.loads(raw)
                            if not isinstance(payload, dict):
                                raise McpValidationError(
                                    "MCP SSE response is not a JSON object"
                                )
                            if payload.get("id") == request_id:
                                return cast(dict[str, Any], payload)

                    initialized = await request(
                        1,
                        "initialize",
                        {
                            "protocolVersion": protocol_version,
                            "capabilities": {},
                            "clientInfo": {"name": "neomua-runtime", "version": "0.5"},
                        },
                    )
                    if initialized.get("error"):
                        raise McpValidationError("MCP initialize failed")
                    notified = await client.post(
                        message_endpoint,
                        headers=headers,
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    )
                    notified.raise_for_status()
                    listed = await request(2, "tools/list", {})
                    tools = listed.get("result", {}).get("tools")
                    if not isinstance(tools, list):
                        raise McpValidationError(
                            "MCP tools/list returned an invalid result"
                        )
                    return [
                        {
                            "name": item.get("name"),
                            "description": item.get("description", ""),
                            "input_schema": item.get("inputSchema", {}),
                        }
                        for item in tools
                    ]
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)

    async def _validate_http(
        self,
        config: dict[str, Any],
        secret_inputs: dict[str, str],
        protocol_version: str,
    ) -> list[dict[str, Any]]:
        endpoint = str(config["endpoint"])
        await _assert_public_endpoint(endpoint)
        headers = dict(secret_inputs)
        timeout = httpx.Timeout(
            float(config.get("call_timeout_seconds", 30)),
            connect=float(config.get("connect_timeout_seconds", 10)),
        )
        maximum_bytes = int(config.get("max_result_bytes", 10 * 1024 * 1024))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            request_headers = {
                **headers,
                "Accept": "application/json, text/event-stream",
            }
            initialized = await client.post(
                endpoint,
                headers=request_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": protocol_version,
                        "capabilities": {},
                        "clientInfo": {"name": "neomua-runtime", "version": "0.5"},
                    },
                },
            )
            if initialized.is_redirect:
                raise McpValidationError("MCP redirect is forbidden")
            initialized.raise_for_status()
            payload = _streamable_response(initialized, 1, maximum_bytes)
            if payload.get("error") or not isinstance(payload.get("result"), dict):
                raise McpValidationError("MCP initialize failed")
            session_id = initialized.headers.get("mcp-session-id")
            if session_id:
                headers["mcp-session-id"] = session_id
                request_headers["mcp-session-id"] = session_id
            notification = await client.post(
                endpoint,
                headers=request_headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            notification.raise_for_status()
            listed = await client.post(
                endpoint,
                headers=request_headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            listed.raise_for_status()
            tools = (
                _streamable_response(listed, 2, maximum_bytes)
                .get("result", {})
                .get("tools")
            )
            if not isinstance(tools, list):
                raise McpValidationError("MCP tools/list returned an invalid result")
            return [
                {
                    "name": item.get("name"),
                    "description": item.get("description", ""),
                    "input_schema": item.get("inputSchema", {}),
                }
                for item in tools
            ]

    async def _validate_stdio(
        self,
        config: dict[str, Any],
        secret_inputs: dict[str, str],
        protocol_version: str,
    ) -> list[dict[str, Any]]:
        key = str(config["executable_key"])
        executable = self.executable_registry.get(key)
        if not executable or not os.path.isabs(executable):
            raise McpValidationError(
                "MCP executable key is not registered to an absolute path"
            )
        process_env = {"LANG": os.environ.get("LANG", "C.UTF-8"), **secret_inputs}
        process = await asyncio.create_subprocess_exec(
            executable,
            *config.get("args", []),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
        )
        assert process.stdin is not None and process.stdout is not None
        stdin = process.stdin
        stdout = process.stdout

        async def request(
            request_id: int, method: str, params: dict[str, Any]
        ) -> dict[str, Any]:
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
                separators=(",", ":"),
            ).encode()
            stdin.write(payload + b"\n")
            await stdin.drain()
            while True:
                line = await asyncio.wait_for(
                    stdout.readline(),
                    timeout=float(config.get("call_timeout_seconds", 30)),
                )
                if not line:
                    raise McpValidationError(
                        "MCP stdio process closed before responding"
                    )
                if len(line) > int(config.get("max_result_bytes", 10 * 1024 * 1024)):
                    raise McpValidationError("MCP stdio response size is invalid")
                response = json.loads(line)
                if not isinstance(response, dict):
                    raise McpValidationError("MCP stdio response is not a JSON object")
                if response.get("id") == request_id:
                    return cast(dict[str, Any], response)

        try:
            initialized = await request(
                1,
                "initialize",
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "neomua-runtime", "version": "0.5"},
                },
            )
            if initialized.get("error"):
                raise McpValidationError("MCP initialize failed")
            stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
            await stdin.drain()
            listed = await request(2, "tools/list", {})
            tools = listed.get("result", {}).get("tools")
            if not isinstance(tools, list):
                raise McpValidationError("MCP tools/list returned an invalid result")
            return [
                {
                    "name": item.get("name"),
                    "description": item.get("description", ""),
                    "input_schema": item.get("inputSchema", {}),
                }
                for item in tools
            ]
        finally:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
