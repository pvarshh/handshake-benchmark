"""Degraded counterpart servers for S4b (malformed metadata) and S4c (version
mismatch), driven by env vars:

  PROTO = mcp | a2a | acp
  MODE  = truncated | badversion
  PORT  = listen port

`truncated` serves syntactically invalid JSON: the valid metadata body cut at
50% (correct Content-Length for the bytes actually sent, valid HTTP framing —
the failure surfaces at JSON parse time, not transport time).

`badversion` (MCP only here; A2A uses the real server with BAD_VERSION=1):
  - `server/discover` is answered with JSON-RPC error -32022
    (unsupported protocol version) advertising only the fictional version
    2099-01-01;
  - `initialize` is answered with an otherwise-valid result carrying
    protocolVersion 2099-01-01.
"""

import json
import os

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

ROOT = os.path.dirname(os.path.abspath(__file__))
PROTO = os.environ["PROTO"]
MODE = os.environ["MODE"]
PORT = int(os.environ.get("PORT", "9111"))


def load(name: str) -> str:
    with open(os.path.join(ROOT, "metadata", name)) as f:
        return f.read().strip()


def truncate(body: str) -> str:
    return body[: len(body) // 2]


async def a2a_card(request: Request) -> Response:
    return Response(truncate(load("a2a_card.json")), media_type="application/json")


async def acp_agents(request: Request) -> Response:
    return Response(truncate(load("acp_manifest.json")), media_type="application/json")


async def mcp_endpoint(request: Request) -> Response:
    try:
        payload = json.loads(await request.body())
    except json.JSONDecodeError:
        payload = {}
    method = payload.get("method", "")
    req_id = payload.get("id", 1)

    if MODE == "truncated":
        if method == "server/discover":
            template = load("mcp_discover_result.json")
        elif method == "initialize":
            template = load("mcp_initialize_result.json")
        else:
            template = load("mcp_tools_list_modern.json")
        body = json.loads(template)
        body["id"] = req_id
        return Response(truncate(json.dumps(body)), media_type="application/json")

    # MODE == "badversion"
    if method == "server/discover":
        err = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32022,
                "message": "Unsupported protocol version",
                "data": {"supported": ["2099-01-01"]},
            },
        }
        return Response(json.dumps(err), media_type="application/json")
    if method == "initialize":
        body = json.loads(load("mcp_initialize_result.json"))
        body["id"] = req_id
        body["result"]["protocolVersion"] = "2099-01-01"
        return Response(json.dumps(body), media_type="application/json")
    if method.startswith("notifications/"):
        return Response(status_code=202)
    err = {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32600, "message": "Bad Request"},
    }
    return Response(json.dumps(err), media_type="application/json", status_code=400)


routes = {
    "a2a": [Route("/.well-known/agent-card.json", a2a_card, methods=["GET"])],
    "acp": [Route("/agents", acp_agents, methods=["GET"])],
    "mcp": [Route("/mcp", mcp_endpoint, methods=["POST", "GET", "DELETE"])],
}

app = Starlette(routes=routes[PROTO])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
