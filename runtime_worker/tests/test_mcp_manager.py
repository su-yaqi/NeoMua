import json

import httpx
import pytest

from runtime_worker.mcp_manager import McpRuntimeManager, _streamable_response


def test_streamable_http_accepts_sse_json_rpc_response() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n\n',
    )
    assert _streamable_response(response, 2, 1024)["result"] == {"tools": []}


@pytest.mark.anyio
async def test_stdio_uses_newline_delimited_json_rpc(tmp_path) -> None:
    executable = tmp_path / "mcp-server"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    value = json.loads(line)\n"
        "    if value.get('method') == 'initialize':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':value['id'],'result':{'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'test','version':'1'}}}), flush=True)\n"
        "    elif value.get('method') == 'tools/list':\n"
        "        print(json.dumps({'jsonrpc':'2.0','id':value['id'],'result':{'tools':[{'name':'echo','inputSchema':{'type':'object'}}]}}), flush=True)\n",
        "utf-8",
    )
    executable.chmod(0o700)
    manager = McpRuntimeManager(executable_registry={"test": str(executable)})
    tools = await manager.validate(
        {
            "transport": "stdio",
            "config": {"executable_key": "test", "call_timeout_seconds": 5},
            "protocol_version": "2025-06-18",
            "secret_inputs": {},
        }
    )
    assert json.loads(json.dumps(tools)) == [
        {"name": "echo", "description": "", "input_schema": {"type": "object"}}
    ]
