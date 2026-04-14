"""
Streamable HTTP transport for Skills Data MCP Server.

Implements the MCP Streamable HTTP specification:
- POST /mcp  — JSON-RPC requests (tools/list, tools/call, initialize, etc.)
- GET  /mcp  — optional persistent SSE stream for server notifications
- GET  /healthz — health check
"""

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import FastAPI, Request, Response, Header
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.status import HTTP_202_ACCEPTED, HTTP_400_BAD_REQUEST, HTTP_500_INTERNAL_SERVER_ERROR
import uvicorn

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global — set at startup
_mcp_app: Optional[FastMCP] = None

fastapi_app = FastAPI(title="Skills Data MCP Server")


# ─── JSON-RPC helpers ─────────────────────────────────────────────────────────

def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _is_notification(body: dict) -> bool:
    return "id" not in body


# ─── MCP request dispatch ─────────────────────────────────────────────────────

async def _handle_request(body: dict) -> Optional[dict]:
    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    try:
        if method == "initialize":
            return _ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "skills-data-mcp-server", "version": "0.1.0"},
            })

        elif method == "ping":
            return None

        elif method == "notifications/initialized":
            return None

        elif method == "tools/list":
            tools = await _mcp_app.list_tools()
            return _ok(req_id, {"tools": [t.model_dump() for t in tools]})

        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            if not name:
                return _err(req_id, -32602, "Missing tool name")

            raw = await _mcp_app.call_tool(name, args)

            # Normalize result to MCP content format
            # FastMCP returns a list of TextContent/content objects
            if isinstance(raw, list):
                content = [
                    {"type": getattr(item, "type", "text"), "text": getattr(item, "text", str(item))}
                    if not isinstance(item, dict)
                    else item
                    for item in raw
                ]
            elif isinstance(raw, dict) and "content" in raw and isinstance(raw["content"], list):
                content = raw["content"]
            else:
                text = json.dumps(raw, indent=2, ensure_ascii=False) if not isinstance(raw, str) else raw
                content = [{"type": "text", "text": text}]

            return _ok(req_id, {"content": content})

        else:
            return _err(req_id, -32601, f"Unknown method: {method}")

    except FileNotFoundError as e:
        return _err(req_id, -32603, str(e))
    except ValueError as e:
        return _err(req_id, -32602, str(e))
    except Exception as e:
        logger.error(f"Error handling {method}: {e}", exc_info=True)
        return _err(req_id, -32603, str(e))


# ─── Endpoints ────────────────────────────────────────────────────────────────

@fastapi_app.post("/mcp")
async def mcp_post(
    request: Request,
    mcp_protocol_version: Optional[str] = Header(None, alias="MCP-Protocol-Version"),
):
    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=HTTP_400_BAD_REQUEST, content=_err(None, -32700, f"Parse error: {e}"))

    if not isinstance(body, dict) or "method" not in body:
        return JSONResponse(status_code=HTTP_400_BAD_REQUEST, content=_err(body.get("id"), -32600, "Invalid JSON-RPC request"))

    if _is_notification(body):
        asyncio.create_task(_handle_request(body))
        return Response(status_code=HTTP_202_ACCEPTED)

    response = await _handle_request(body)
    if response is None:
        return Response(status_code=HTTP_202_ACCEPTED)
    return JSONResponse(content=response)


@fastapi_app.get("/mcp")
async def mcp_get():
    """Optional SSE stream for server-initiated notifications."""
    async def _keepalive():
        while True:
            yield ": keepalive\n\n"
            await asyncio.sleep(30)

    return StreamingResponse(
        _keepalive(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@fastapi_app.get("/healthz")
async def healthz():
    if _mcp_app:
        return JSONResponse({"status": "healthy"})
    return JSONResponse(status_code=HTTP_500_INTERNAL_SERVER_ERROR, content={"status": "unhealthy"})


# ─── Entry ────────────────────────────────────────────────────────────────────

def run_http_server(app: FastMCP, host: str = "0.0.0.0", port: int = 6666):
    global _mcp_app
    _mcp_app = app
    logger.info(f"Skills Data MCP Server starting on http://{host}:{port}/mcp")
    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")
