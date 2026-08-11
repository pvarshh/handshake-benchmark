"""Minimal A2A counterpart agent: Agent Card + one `echo` skill over JSON-RPC.

Degraded modes (env vars):
  SLOW_MS       - artificial per-request delay in milliseconds (S4a)
  BAD_VERSION   - advertise an unsupported protocol version in the card (S4c)
"""

import asyncio
import os

import uvicorn
from fastapi import FastAPI

from a2a.helpers import get_message_text, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

PORT = int(os.environ.get("PORT", "9102"))
SLOW_MS = int(os.environ.get("SLOW_MS", "0"))
BAD_VERSION = os.environ.get("BAD_VERSION", "") == "1"


class EchoExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        text = get_message_text(context.message) if context.message else ""
        await event_queue.enqueue_event(
            new_text_message(text, context_id=context.context_id, task_id=None)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


def build_card() -> AgentCard:
    return AgentCard(
        name="echo-agent",
        description="A minimal agent exposing a single echo capability for benchmarking.",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=f"http://127.0.0.1:{PORT}/",
                protocol_binding="JSONRPC",
                protocol_version="99.0.0" if BAD_VERSION else "1.0.0",
            )
        ],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="echo",
                name="Echo",
                description="Echo a message back verbatim.",
                tags=["utility"],
            )
        ],
    )


def build_app() -> FastAPI:
    card = build_card()
    handler = DefaultRequestHandler(
        agent_executor=EchoExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, "/"),
    )
    return app


class SlowASGI:
    """ASGI wrapper that delays every HTTP response by a fixed amount (S4a)."""

    def __init__(self, app, delay_s: float):
        self.app = app
        self.delay_s = delay_s

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        await self.app(scope, receive, send)


app = build_app()
if SLOW_MS:
    app = SlowASGI(app, SLOW_MS / 1000)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
