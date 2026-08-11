"""RTT sweep: S1 cold-start handshake latency vs emulated RTT.

Runs the four protocol variants at emulated RTT in {0, 25, 50, 100} ms
(relay delay = RTT/2 per direction), N=20 measured runs per cell after 3
warmups, and writes one JSON line per measured run to
results/raw/sweep.jsonl. Companion to harness/scenarios.py (S1 cells).

  python -m harness.rtt_sweep
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.proxy import CountingProxy
from harness.runners import run_a2a, run_acp, run_mcp
from harness.scenarios import start_server

RTTS_MS = [0, 25, 50, 100]
N = 20
WARMUP = 3
_ports = itertools.count(9600)

VARIANTS = [
    ("mcp-legacy", "mcp_server.py", run_mcp, {"mode": "legacy"}),
    ("mcp-modern", "mcp_server.py", run_mcp, {"mode": "auto"}),
    ("a2a", "a2a_server.py", run_a2a, {}),
    ("acp", "acp_server.py", run_acp, {}),
]


async def sweep_variant(name, script, runner, extra) -> list[dict]:
    rows = []
    for rtt in RTTS_MS:
        port = next(_ports)
        proc = start_server(script, port, None)
        try:
            if True:
                delay = rtt / 2000.0  # ms RTT -> seconds per direction
                for i in range(-WARMUP, N):
                    proxy = CountingProxy(
                        "127.0.0.1", port, delay_c2s=delay, delay_s2c=delay
                    )
                    await proxy.start()
                    try:
                        kwargs = dict(extra)
                        if runner is run_a2a:
                            kwargs["direct_url"] = f"http://127.0.0.1:{port}"
                        row = await runner(
                            proxy=proxy,
                            scenario="sweep",
                            network=f"rtt{rtt}",
                            idx=i,
                            protocol_label=name,
                            validate=False,
                            **kwargs,
                        )
                    finally:
                        await proxy.stop()
                    if i >= 0:
                        rows.append(
                            {
                                "protocol": name,
                                "rtt_ms": rtt,
                                "idx": i,
                                "handshake_ms": row["handshake_ms"],
                                "round_trips": row["round_trips"],
                                "outcome": row["outcome"],
                                "error": row["error"]["type"] if row["error"] else None,
                            }
                        )
                print(f"{name} rtt={rtt}ms done", flush=True)
        finally:
            proc.terminate()
    return rows


def summarize(rows: list[dict]) -> None:
    for name, _, _, _ in VARIANTS:
        meds = []
        for rtt in RTTS_MS:
            ms = sorted(
                r["handshake_ms"]
                for r in rows
                if r["protocol"] == name and r["rtt_ms"] == rtt
                and r["handshake_ms"] is not None
            )
            med = statistics.median(ms)
            p95 = ms[min(len(ms) - 1, int(round(0.95 * len(ms))) - 1)]
            meds.append((rtt, med))
            print(f"{name} rtt={rtt}: n={len(ms)} med={med:.1f} p95={p95:.1f}")
        # OLS slope of median vs rtt
        xs = [m[0] for m in meds]
        ys = [m[1] for m in meds]
        xbar, ybar = sum(xs) / len(xs), sum(ys) / len(ys)
        slope = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys)) / sum(
            (x - xbar) ** 2 for x in xs
        )
        print(f"{name} slope: {slope:.2f} ms latency per ms RTT")


if __name__ == "__main__":
    out = os.path.join("results", "raw", "sweep.jsonl")
    rows = []
    for variant in VARIANTS:
        # fresh event loop per protocol variant, mirroring harness isolation
        rows.extend(asyncio.run(sweep_variant(*variant)))
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    summarize(rows)
