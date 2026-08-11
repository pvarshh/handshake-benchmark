"""Spike: boot each counterpart agent, run one instrumented handshake + echo
validation against it, and print the observed request sequence.
Run: .venv/bin/python -m harness.spike_check
"""

import asyncio
import os
import socket
import subprocess
import sys
import time

from harness.instrument import Recorder, make_client
from harness.proxy import CountingProxy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable


def wait_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise TimeoutError(f"port {port} not up")


def start_server(script: str, port: int, env_extra=None) -> subprocess.Popen:
    env = dict(os.environ, PORT=str(port))
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(
        [PY, os.path.join(ROOT, "agents", script)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    wait_port(port)
    return proc


def dump(recorder: Recorder, proxy: CountingProxy, label: str):
    print(f"\n=== {label} ===")
    for r in recorder.records:
        print(
            f"  #{r.seq} [{r.phase}] {r.method} {r.path} -> {r.status} "
            f"req={r.req_body_bytes}B resp={r.resp_body_bytes}B "
            f"ct={r.content_type.split(';')[0]} err={r.error}"
        )
    s = proxy.stats
    print(
        f"  proxy: conns={s.connections} c2s={s.bytes_c2s}B s2c={s.bytes_s2c}B"
    )


async def spike_mcp(port: int):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    rec = Recorder()
    proxy = CountingProxy("127.0.0.1", port)
    ppt = await proxy.start()
    async with make_client(rec) as hc:
        rec.set_phase("session")
        async with streamable_http_client(
            f"http://127.0.0.1:{ppt}/mcp", http_client=hc
        ) as streams:
            read, write = streams[0], streams[1]
            extra = streams[2:] if len(streams) > 2 else ()
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                rec.set_phase("capability")
                tools = await session.list_tools()
                names = [t.name for t in tools.tools]
                rec.set_phase("task-execution")
                result = await session.call_tool("echo", {"text": "hello"})
                rec.set_phase("teardown")
                print(
                    "MCP init:",
                    init.server_info,
                    "| tools:",
                    names,
                    "| extra streams elems:",
                    len(extra),
                )
                print("MCP echo result:", result.content[0].text)
    await proxy.stop()
    dump(rec, proxy, "MCP")


async def spike_a2a(port: int):
    from a2a.client.card_resolver import A2ACardResolver
    from a2a.client.client_factory import ClientConfig, ClientFactory
    from a2a.helpers import new_text_message
    from a2a.types import SendMessageRequest

    rec = Recorder()
    proxy = CountingProxy("127.0.0.1", port)
    ppt = await proxy.start()
    async with make_client(rec) as hc:
        rec.set_phase("discovery")
        resolver = A2ACardResolver(hc, f"http://127.0.0.1:{ppt}")
        card = await resolver.get_agent_card()
        skills = [s.id for s in card.skills]
        print("A2A card:", card.name, "| skills:", skills, "| interfaces:",
              [(i.url, i.protocol_binding, i.protocol_version) for i in card.supported_interfaces])
        rec.set_phase("task-execution")
        # Point the client at the proxy, not the card-advertised direct URL
        for iface in card.supported_interfaces:
            iface.url = f"http://127.0.0.1:{ppt}/"
        factory = ClientFactory(ClientConfig(httpx_client=hc, streaming=False))
        client = factory.create(card)
        req = SendMessageRequest(message=new_text_message("hello"))
        async for resp in client.send_message(req):
            print("A2A resp type:", type(resp).__name__, str(resp)[:200].replace("\n", " "))
        rec.set_phase("teardown")
    await proxy.stop()
    dump(rec, proxy, "A2A")


async def spike_acp(port: int):
    from acp_sdk.client import Client

    rec = Recorder()
    proxy = CountingProxy("127.0.0.1", port)
    ppt = await proxy.start()
    async with make_client(rec, base_url=f"http://127.0.0.1:{ppt}") as hc:
        async with Client(client=hc, manage_client=False) as client:
            rec.set_phase("discovery")
            agents = [a async for a in client.agents()]
            print("ACP agents:", [(a.name, a.description) for a in agents])
            rec.set_phase("task-execution")
            run = await client.run_sync("hello", agent="echo-agent")
            print("ACP run:", run.status, [str(p.content)[:40] for m in run.output for p in m.parts])
            rec.set_phase("teardown")
    await proxy.stop()
    dump(rec, proxy, "ACP")


async def main():
    procs = []
    try:
        procs.append(start_server("mcp_server.py", 9101))
        procs.append(start_server("a2a_server.py", 9102))
        procs.append(start_server("acp_server.py", 9103))
        for name, fn, port in [
            ("mcp", spike_mcp, 9101),
            ("a2a", spike_a2a, 9102),
            ("acp", spike_acp, 9103),
        ]:
            try:
                await fn(port)
            except Exception as e:
                import traceback
                print(f"\n!!! {name} FAILED: {type(e).__name__}: {e}")
                traceback.print_exc()
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                err = p.communicate(timeout=5)[1]
                if err and b"Traceback" in err:
                    print("SERVER STDERR:", err.decode()[:2000])
            except Exception:
                p.kill()


if __name__ == "__main__":
    asyncio.run(main())
