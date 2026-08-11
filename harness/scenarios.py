"""Scenario orchestration: cell table (scenario x protocol variant), server
lifecycle, warm-state priming, warmup runs, and JSONL output.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from functools import partial

from harness.proxy import CountingProxy
from harness.runners import run_a2a, run_acp, run_mcp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
WARMUP = 5
_ports = itertools.count(9300)

NETWORK_DELAYS = {"local": 0.0, "rtt50": 0.025}  # per direction


def wait_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise TimeoutError(f"server on port {port} did not start")


def start_server(script: str, port: int, env_extra: dict | None) -> subprocess.Popen:
    env = dict(os.environ, PORT=str(port))
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(
        [PY, os.path.join(ROOT, "agents", script)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_port(port)
    time.sleep(0.5)
    return proc


@dataclass
class Cell:
    scenario: str
    protocol: str  # label in results
    family: str  # mcp | a2a | acp
    server_script: str
    server_env: dict = field(default_factory=dict)
    runner_kwargs: dict = field(default_factory=dict)
    networks: tuple = ("local", "rtt50")
    warm: str | None = None  # None | mcp-store | a2a-card | acp-manifest
    validate: bool = True


CELLS: list[Cell] = [
    # S1 - cold start
    Cell("s1", "mcp-legacy", "mcp", "mcp_server.py", runner_kwargs={"mode": "legacy"}),
    Cell("s1", "mcp-modern", "mcp", "mcp_server.py", runner_kwargs={"mode": "auto"}),
    Cell("s1", "a2a", "a2a", "a2a_server.py"),
    Cell("s1", "acp", "acp", "acp_server.py"),
    # S2 - warm repeat
    Cell("s2", "mcp-legacy-warm", "mcp", "mcp_server.py",
         runner_kwargs={"mode": "legacy"}, warm="mcp-store"),
    Cell("s2", "mcp-modern-warm", "mcp", "mcp_server.py",
         runner_kwargs={"mode": "auto"}, warm="mcp-store"),
    Cell("s2", "mcp-pinned-warm", "mcp", "mcp_server.py",
         runner_kwargs={"mode": "2026-07-28"}, warm="mcp-store"),
    Cell("s2", "a2a-warm", "a2a", "a2a_server.py", warm="a2a-card"),
    Cell("s2", "acp-warm", "acp", "acp_server.py", warm="acp-manifest"),
    # S3 - capability mismatch (time-to-rejection)
    Cell("s3", "mcp-legacy", "mcp", "mcp_server.py",
         runner_kwargs={"mode": "legacy", "required": "translate"}, validate=False),
    Cell("s3", "mcp-modern", "mcp", "mcp_server.py",
         runner_kwargs={"mode": "auto", "required": "translate"}, validate=False),
    Cell("s3", "a2a", "a2a", "a2a_server.py",
         runner_kwargs={"required": "translate"}, validate=False),
    Cell("s3", "acp", "acp", "acp_server.py",
         runner_kwargs={"required": "translate-agent"}, validate=False),
    # S4a - slow counterpart (2s per response); localhost only: the injected
    # delay dominates and the added 50ms emulation adds nothing interpretable
    Cell("s4a", "mcp-legacy", "mcp", "mcp_server.py", {"SLOW_MS": "2000"},
         runner_kwargs={"mode": "legacy"}, networks=("local",), validate=False),
    Cell("s4a", "mcp-modern", "mcp", "mcp_server.py", {"SLOW_MS": "2000"},
         runner_kwargs={"mode": "auto"}, networks=("local",), validate=False),
    Cell("s4a", "a2a", "a2a", "a2a_server.py", {"SLOW_MS": "2000"},
         networks=("local",), validate=False),
    Cell("s4a", "acp", "acp", "acp_server.py", {"SLOW_MS": "2000"},
         networks=("local",), validate=False),
    # S4b - malformed capability metadata (truncated JSON)
    Cell("s4b", "mcp-legacy", "mcp", "degraded.py", {"PROTO": "mcp", "MODE": "truncated"},
         runner_kwargs={"mode": "legacy"}, validate=False),
    Cell("s4b", "mcp-modern", "mcp", "degraded.py", {"PROTO": "mcp", "MODE": "truncated"},
         runner_kwargs={"mode": "auto"}, validate=False),
    Cell("s4b", "a2a", "a2a", "degraded.py", {"PROTO": "a2a", "MODE": "truncated"},
         validate=False),
    Cell("s4b", "acp", "acp", "degraded.py", {"PROTO": "acp", "MODE": "truncated"},
         validate=False),
    # S4c - version mismatch (ACP: not testable, no in-protocol version signal)
    Cell("s4c", "mcp-legacy", "mcp", "degraded.py", {"PROTO": "mcp", "MODE": "badversion"},
         runner_kwargs={"mode": "legacy"}, validate=False),
    Cell("s4c", "mcp-modern", "mcp", "degraded.py", {"PROTO": "mcp", "MODE": "badversion"},
         runner_kwargs={"mode": "auto"}, validate=False),
    Cell("s4c", "a2a", "a2a", "a2a_server.py", {"BAD_VERSION": "1"}, validate=True),
]


async def prime_warm_state(cell: Cell, server_port: int):
    """Perform the untimed first interaction that leaves warm state behind."""
    url = f"http://127.0.0.1:{server_port}"
    if cell.warm == "mcp-store":
        from mcp.client.caching import CacheConfig, InMemoryResponseCacheStore
        from mcp.client.client import Client
        from mcp.client.streamable_http import streamable_http_client

        store = InMemoryResponseCacheStore()
        async with Client(
            streamable_http_client(f"{url}/mcp"),
            mode="auto",
            cache=CacheConfig(store=store, partition="bench", target_id="echo-agent"),
        ) as client:
            await client.list_tools()
        return {"store": store}
    if cell.warm == "a2a-card":
        import httpx

        from a2a.client.card_resolver import A2ACardResolver

        async with httpx.AsyncClient() as hc:
            card = await A2ACardResolver(hc, url).get_agent_card()
        return {"warm_card": card}
    if cell.warm == "acp-manifest":
        from acp_sdk.client import Client as ACPClient

        async with ACPClient(base_url=url) as client:
            agents = [a async for a in client.agents()]
        return {"warm_manifest": agents}
    return {}


async def run_cell(cell: Cell, network: str, n: int, out_dir: str) -> str:
    delay = NETWORK_DELAYS[network]
    port = next(_ports)
    proc = start_server(cell.server_script, port, cell.server_env)
    rows = []
    try:
        warm_kwargs = await prime_warm_state(cell, port) if cell.warm else {}
        runner = {"mcp": run_mcp, "a2a": run_a2a, "acp": run_acp}[cell.family]
        extra = dict(cell.runner_kwargs)
        if cell.family == "mcp" and "store" in warm_kwargs:
            extra["store"] = warm_kwargs["store"]
        elif cell.family == "a2a":
            extra["direct_url"] = f"http://127.0.0.1:{port}"
            if "warm_card" in warm_kwargs:
                extra["warm_card"] = warm_kwargs["warm_card"]
        elif cell.family == "acp" and "warm_manifest" in warm_kwargs:
            extra["warm_manifest"] = warm_kwargs["warm_manifest"]

        for i in range(-WARMUP, n):
            proxy = CountingProxy("127.0.0.1", port, delay_c2s=delay, delay_s2c=delay)
            await proxy.start()
            try:
                row = await runner(
                    proxy=proxy,
                    scenario=cell.scenario,
                    network=network,
                    idx=i,
                    protocol_label=cell.protocol,
                    validate=cell.validate,
                    **extra,
                )
            finally:
                await proxy.stop()
            if i >= 0:
                rows.append(row)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{cell.scenario}.{cell.protocol}.{network}.jsonl")
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path
