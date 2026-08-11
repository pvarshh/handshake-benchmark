"""CLI entry point.

  python -m harness.run --scenario all --network both --n 50 --out results/raw

Runs every matching cell sequentially and prints progress. Reproduces the
paper's raw data with the default arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata as im
import json
import os
import platform
import sys
import time

from harness.scenarios import CELLS, run_cell


def write_meta(out_dir: str) -> None:
    meta = {
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "packages": {
            p: im.version(p)
            for p in [
                "mcp", "a2a-sdk", "acp-sdk", "httpx", "uvicorn",
                "fastapi", "starlette", "pydantic",
            ]
        },
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all",
                    help="all | s1 | s2 | s3 | s4a | s4b | s4c (comma-separated ok)")
    ap.add_argument("--protocol", default="all",
                    help="all | substring filter on protocol label")
    ap.add_argument("--network", default="both", help="both | local | rtt50")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--out", default="results/raw")
    args = ap.parse_args()

    scenarios = None if args.scenario == "all" else set(args.scenario.split(","))
    networks = ("local", "rtt50") if args.network == "both" else (args.network,)

    write_meta(args.out)
    t0 = time.time()
    for cell in CELLS:
        if scenarios and cell.scenario not in scenarios:
            continue
        if args.protocol != "all" and args.protocol not in cell.protocol:
            continue
        for network in cell.networks:
            if network not in networks:
                continue
            t = time.time()
            path = await run_cell(cell, network, args.n, args.out)
            with open(path) as f:
                rows = [json.loads(line) for line in f]
            outcomes = {}
            for r in rows:
                outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
            med = sorted(
                r["handshake_ms"] for r in rows if r["handshake_ms"] is not None
            )
            med_ms = med[len(med) // 2] if med else float("nan")
            print(
                f"[{time.time() - t0:7.1f}s] {cell.scenario}.{cell.protocol}.{network}: "
                f"n={len(rows)} outcomes={outcomes} median={med_ms:.1f}ms "
                f"({time.time() - t:.1f}s)",
                flush=True,
            )
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
