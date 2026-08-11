"""Minimal MCP counterpart agent: one `echo` tool, streamable HTTP transport.

Degraded modes (env vars):
  SLOW_MS       - artificial per-request delay in milliseconds (S4a)
"""

import asyncio
import os

import uvicorn
from mcp.server.caching import CacheHint
from mcp.server.mcpserver import MCPServer

# The counterpart explicitly permits clients to cache discovery results and
# the tool list for one hour (SEP-2549 freshness hints). This is the
# configuration a latency-conscious deployment would choose; the default is
# ttl_ms=0 (no caching).
CACHE_TTL_MS = 60 * 60 * 1000

server = MCPServer(
    name="echo-agent",
    version="1.0.0",
    instructions="A minimal agent exposing a single echo capability for benchmarking.",
    cache_hints={
        "server/discover": CacheHint(ttl_ms=CACHE_TTL_MS, scope="public"),
        "tools/list": CacheHint(ttl_ms=CACHE_TTL_MS, scope="public"),
    },
)


@server.tool()
def echo(text: str) -> str:
    """Echo a message back verbatim."""
    return text


class SlowASGI:
    """ASGI wrapper that delays every HTTP response by a fixed amount (S4a)."""

    def __init__(self, app, delay_s: float):
        self.app = app
        self.delay_s = delay_s

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        await self.app(scope, receive, send)


app = server.streamable_http_app()

SLOW_MS = int(os.environ.get("SLOW_MS", "0"))
if SLOW_MS:
    app = SlowASGI(app, SLOW_MS / 1000)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9101"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
