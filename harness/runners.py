"""Per-protocol handshake runners.

Each runner performs one complete run: handshake to task-ready (timed),
optional validation echo call (recorded, excluded from handshake metrics),
and returns a JSONL-ready row. All wire traffic goes through the counting
proxy; all HTTP goes through the instrumented httpx client.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from harness.instrument import Recorder, RequestRecord, make_client
from harness.proxy import CountingProxy, ProxyStats

RUN_TIMEOUT_S = 30.0


def _rpc_method(rec: RequestRecord) -> str:
    if not rec.req_body:
        return ""
    try:
        return json.loads(rec.req_body).get("method", "")
    except Exception:
        return ""


def classify_mcp(rec: RequestRecord) -> str:
    m = _rpc_method(rec)
    if rec.method == "DELETE":
        return "teardown"
    if m == "initialize":
        return "session"
    if m == "notifications/initialized":
        return "readiness"
    if m == "server/discover":
        return "capability"
    if m == "tools/list":
        return "capability"
    if m in ("tools/call",) or m.startswith("notifications/cancelled"):
        return "task-execution"
    return rec.phase


def classify_a2a(rec: RequestRecord) -> str:
    if "agent-card" in rec.path or "agent.json" in rec.path:
        return "discovery"
    return "task-execution"


def classify_acp(rec: RequestRecord) -> str:
    if rec.path.endswith("/agents") and rec.method == "GET":
        return "discovery"
    return "task-execution"


CLASSIFIERS = {"mcp": classify_mcp, "a2a": classify_a2a, "acp": classify_acp}

HANDSHAKE_PHASES = ("discovery", "capability", "session", "readiness")


def build_row(
    *,
    protocol: str,
    scenario: str,
    network: str,
    idx: int,
    family: str,
    outcome: str,
    t0_ns: int,
    t_ready_ns: int | None,
    recorder: Recorder,
    stats: ProxyStats | None,
    error: dict | None = None,
    validated: bool | None = None,
    notes: dict | None = None,
) -> dict:
    classify = CLASSIFIERS[family]
    reqs = []
    phases: dict[str, dict] = {}
    seen: dict[tuple, int] = {}
    for rec in recorder.records:
        phase = classify(rec)
        d = rec.to_dict(t0_ns)
        d["phase"] = phase
        d["rpc_method"] = _rpc_method(rec)
        reqs.append(d)
        if phase in HANDSHAKE_PHASES:
            p = phases.setdefault(
                phase,
                {"round_trips": 0, "ms": 0.0, "bytes_up": 0, "bytes_down": 0},
            )
            p["round_trips"] += 1
            if rec.t_end_ns:
                p["ms"] += (rec.t_end_ns - rec.t_start_ns) / 1e6
            p["bytes_up"] += rec.req_wire_bytes
            p["bytes_down"] += rec.resp_header_bytes + rec.resp_body_bytes
            key = (rec.method, rec.path, d["rpc_method"])
            seen[key] = seen.get(key, 0) + 1
    handshake_reqs = [r for r in reqs if r["phase"] in HANDSHAKE_PHASES]
    retries = sum(v - 1 for v in seen.values())
    return {
        "run_id": uuid.uuid4().hex[:12],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "protocol": protocol,
        "scenario": scenario,
        "network": network,
        "idx": idx,
        "outcome": outcome,
        "handshake_ms": (t_ready_ns - t0_ns) / 1e6 if t_ready_ns else None,
        "round_trips": len(handshake_reqs),
        "retries": retries,
        "connections": stats.connections if stats else None,
        "wire_bytes_up": stats.bytes_c2s if stats else None,
        "wire_bytes_down": stats.bytes_s2c if stats else None,
        "app_bytes_up": sum(r["req_wire_bytes"] for r in handshake_reqs),
        "app_bytes_down": sum(
            r["resp_header_bytes"] + r["resp_body_bytes"] for r in handshake_reqs
        ),
        "phases": phases,
        "validated": validated,
        "error": error,
        "notes": notes or {},
        "requests": reqs,
    }


def _error_info(exc: BaseException) -> dict:
    # Unwrap ExceptionGroups (anyio task groups) to the leaf causes.
    leaves: list[BaseException] = []

    def walk(e: BaseException) -> None:
        if isinstance(e, BaseExceptionGroup):
            for sub in e.exceptions:
                walk(sub)
        else:
            leaves.append(e)

    walk(exc)
    leaf = leaves[0] if leaves else exc
    return {
        "type": type(leaf).__name__,
        "message": str(leaf)[:500],
        "all_types": sorted({type(e).__name__ for e in leaves}),
    }


# ---------------------------------------------------------------- MCP


async def run_mcp(
    *,
    proxy: CountingProxy,
    scenario: str,
    network: str,
    idx: int,
    protocol_label: str,
    mode: str,
    store=None,
    required: str = "echo",
    validate: bool = True,
) -> dict:
    from mcp.client.caching import CacheConfig
    from mcp.client.client import Client
    from mcp.client.streamable_http import streamable_http_client

    recorder = Recorder()
    recorder.set_phase("capability")
    url = f"http://127.0.0.1:{proxy.port}/mcp"
    if store is not None:
        cache = CacheConfig(store=store, partition="bench", target_id="echo-agent")
    else:
        cache = CacheConfig(target_id="echo-agent")

    outcome, t_ready, stats, error, validated = "error", None, None, None, None
    t0 = time.perf_counter_ns()
    try:
        async with asyncio.timeout(RUN_TIMEOUT_S):
            async with make_client(recorder) as hc:
                async with Client(
                    streamable_http_client(url, http_client=hc),
                    mode=mode,
                    cache=cache,
                    raise_exceptions=True,
                ) as client:
                    tools = await client.list_tools()
                    names = {t.name for t in tools.tools}
                    ok = required in names
                    t_ready = time.perf_counter_ns()
                    stats = proxy.snapshot()
                    outcome = "ready" if ok else "rejected"
                    if ok and validate:
                        recorder.set_phase("task-execution")
                        res = await client.call_tool("echo", {"text": "ping"})
                        validated = bool(
                            res.content and res.content[0].text == "ping"
                        )
                    recorder.set_phase("teardown")
    except BaseExceptionGroup as exc:
        error = _error_info(exc)
        stats = stats or proxy.snapshot()
    except TimeoutError:
        error = {"type": "Timeout", "message": f">{RUN_TIMEOUT_S}s", "all_types": ["Timeout"]}
        stats = stats or proxy.snapshot()
    except Exception as exc:
        error = _error_info(exc)
        stats = stats or proxy.snapshot()
    t_fail = time.perf_counter_ns()
    return build_row(
        protocol=protocol_label,
        scenario=scenario,
        network=network,
        idx=idx,
        family="mcp",
        outcome=outcome,
        t0_ns=t0,
        t_ready_ns=t_ready if outcome in ("ready", "rejected") else None,
        recorder=recorder,
        stats=stats,
        error=error,
        validated=validated,
        notes={"mode": mode, "time_to_failure_ms": (t_fail - t0) / 1e6 if error else None},
    )


# ---------------------------------------------------------------- A2A


async def run_a2a(
    *,
    proxy: CountingProxy,
    scenario: str,
    network: str,
    idx: int,
    protocol_label: str,
    warm_card=None,
    required: str = "echo",
    validate: bool = True,
    direct_url: str | None = None,
) -> dict:
    from a2a.client.card_resolver import A2ACardResolver
    from a2a.client.client_factory import ClientConfig, ClientFactory
    from a2a.helpers import new_text_message
    from a2a.types import SendMessageRequest

    recorder = Recorder()
    base_url = f"http://127.0.0.1:{proxy.port}"
    outcome, t_ready, stats, error, validated = "error", None, None, None, None
    notes: dict[str, Any] = {"warm": warm_card is not None}
    t0 = time.perf_counter_ns()
    try:
        async with asyncio.timeout(RUN_TIMEOUT_S):
            async with make_client(recorder) as hc:
                if warm_card is not None:
                    card = warm_card
                else:
                    recorder.set_phase("discovery")
                    card = await A2ACardResolver(hc, base_url).get_agent_card()
                skills = {s.id for s in card.skills}
                ok = required in skills
                t_ready = time.perf_counter_ns()
                stats = proxy.snapshot()
                outcome = "ready" if ok else "rejected"
                notes["card_protocol_versions"] = [
                    i.protocol_version for i in card.supported_interfaces
                ]
                if ok and validate:
                    recorder.set_phase("task-execution")
                    from copy import deepcopy

                    vcard = deepcopy(card)
                    for iface in vcard.supported_interfaces:
                        iface.url = (direct_url or base_url) + "/"
                    factory = ClientFactory(
                        ClientConfig(httpx_client=hc, streaming=False)
                    )
                    client = factory.create(vcard)
                    req = SendMessageRequest(message=new_text_message("ping"))
                    texts = []
                    async for resp in client.send_message(req):
                        if resp.HasField("message"):
                            texts.extend(
                                p.text for p in resp.message.parts if p.HasField("text")
                            )
                    validated = "ping" in texts
                recorder.set_phase("teardown")
    except TimeoutError:
        error = {"type": "Timeout", "message": f">{RUN_TIMEOUT_S}s", "all_types": ["Timeout"]}
        stats = stats or proxy.snapshot()
    except BaseException as exc:
        error = _error_info(exc)
        stats = stats or proxy.snapshot()
    t_fail = time.perf_counter_ns()
    notes["time_to_failure_ms"] = (t_fail - t0) / 1e6 if error else None
    return build_row(
        protocol=protocol_label,
        scenario=scenario,
        network=network,
        idx=idx,
        family="a2a",
        outcome=outcome,
        t0_ns=t0,
        t_ready_ns=t_ready if outcome in ("ready", "rejected") else None,
        recorder=recorder,
        stats=stats,
        error=error,
        validated=validated,
        notes=notes,
    )


# ---------------------------------------------------------------- ACP


async def run_acp(
    *,
    proxy: CountingProxy,
    scenario: str,
    network: str,
    idx: int,
    protocol_label: str,
    warm_manifest=None,
    required: str = "echo-agent",
    validate: bool = True,
) -> dict:
    from acp_sdk.client import Client as ACPClient

    recorder = Recorder()
    base_url = f"http://127.0.0.1:{proxy.port}"
    outcome, t_ready, stats, error, validated = "error", None, None, None, None
    notes: dict[str, Any] = {"warm": warm_manifest is not None}
    t0 = time.perf_counter_ns()
    try:
        async with asyncio.timeout(RUN_TIMEOUT_S):
            async with make_client(recorder, base_url=base_url) as hc:
                async with ACPClient(client=hc, manage_client=False) as client:
                    if warm_manifest is not None:
                        agents = warm_manifest
                    else:
                        recorder.set_phase("discovery")
                        agents = [a async for a in client.agents()]
                    names = {a.name for a in agents}
                    ok = required in names
                    t_ready = time.perf_counter_ns()
                    stats = proxy.snapshot()
                    outcome = "ready" if ok else "rejected"
                    if ok and validate:
                        recorder.set_phase("task-execution")
                        run = await client.run_sync("ping", agent="echo-agent")
                        out = [
                            str(part.content)
                            for m in run.output
                            for part in m.parts
                        ]
                        validated = out == ["ping"]
                    recorder.set_phase("teardown")
    except TimeoutError:
        error = {"type": "Timeout", "message": f">{RUN_TIMEOUT_S}s", "all_types": ["Timeout"]}
        stats = stats or proxy.snapshot()
    except BaseException as exc:
        error = _error_info(exc)
        stats = stats or proxy.snapshot()
    t_fail = time.perf_counter_ns()
    notes["time_to_failure_ms"] = (t_fail - t0) / 1e6 if error else None
    return build_row(
        protocol=protocol_label,
        scenario=scenario,
        network=network,
        idx=idx,
        family="acp",
        outcome=outcome,
        t0_ns=t0,
        t_ready_ns=t_ready if outcome in ("ready", "rejected") else None,
        recorder=recorder,
        stats=stats,
        error=error,
        validated=validated,
        notes=notes,
    )
