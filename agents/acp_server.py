"""Minimal ACP counterpart agent: one `echo` agent behind the REST manifest API.

Degraded modes (env vars):
  SLOW_MS       - artificial per-request delay in milliseconds (S4a)
"""

import asyncio
import os
from collections.abc import AsyncGenerator

import uvicorn
from acp_sdk.models import Message
from acp_sdk.server.agent import agent as acp_agent
from acp_sdk.server.app import create_app

PORT = int(os.environ.get("PORT", "9103"))
SLOW_MS = int(os.environ.get("SLOW_MS", "0"))


@acp_agent(
    name="echo-agent",
    description="A minimal agent exposing a single echo capability for benchmarking.",
    input_content_types=["text/plain"],
    output_content_types=["text/plain"],
)
async def echo_agent(input: list[Message]) -> AsyncGenerator:
    """Echo a message back verbatim."""
    for message in input:
        yield message


class SlowASGI:
    """ASGI wrapper that delays every HTTP response by a fixed amount (S4a)."""

    def __init__(self, app, delay_s: float):
        self.app = app
        self.delay_s = delay_s

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        await self.app(scope, receive, send)


app = create_app(echo_agent)
if SLOW_MS:
    app = SlowASGI(app, SLOW_MS / 1000)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
