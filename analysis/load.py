"""Load raw JSONL results into per-cell summaries."""

from __future__ import annotations

import glob
import json
import os
import random
import statistics


def load_rows(raw_dir: str = "results/raw") -> list[dict]:
    rows = []
    for path in sorted(glob.glob(os.path.join(raw_dir, "*.jsonl"))):
        if os.path.basename(path) in ("baseline.jsonl", "sweep.jsonl"):
            continue
        with open(path) as f:
            for line in f:
                rows.append(json.loads(line))
    return rows


def load_baseline(raw_dir: str = "results/raw") -> dict:
    path = os.path.join(raw_dir, "baseline.jsonl")
    out: dict[str, dict] = {}
    if not os.path.exists(path):
        return out
    groups: dict[str, list[float]] = {}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            groups.setdefault(r["baseline"], []).append(r["ms"])
    for name, vals in groups.items():
        vals.sort()
        out[name] = {
            "n": len(vals),
            "median_ms": statistics.median(vals),
            "p95_ms": vals[min(len(vals) - 1, int(round(0.95 * len(vals))) - 1)],
        }
    return out


def bootstrap_ci(vals: list[float], stat=statistics.median, iters: int = 2000,
                 alpha: float = 0.05, seed: int = 20260810) -> tuple[float, float]:
    """Percentile bootstrap CI for a statistic of `vals`.

    The benchmark's cross-cell client-side setup drift is a few ms, so a
    median quoted to 0.1 ms invites a reader to read differences that the
    noise does not support. Reporting an interval makes the resolution of
    each cell explicit instead of implied. Seeded so reruns are identical.
    """
    if not vals:
        return (float("nan"), float("nan"))
    rnd = random.Random(seed)
    n = len(vals)
    stats = []
    for _ in range(iters):
        stats.append(stat([vals[rnd.randrange(n)] for _ in range(n)]))
    stats.sort()
    lo = stats[max(0, int(round((alpha / 2) * iters)) - 1)]
    hi = stats[min(iters - 1, int(round((1 - alpha / 2) * iters)) - 1)]
    return (lo, hi)


def p95(vals: list[float]) -> float:
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(0.95 * len(s))) - 1))
    return s[idx]


def summarize(rows: list[dict]) -> dict:
    """Group by (scenario, protocol, network) -> summary dict."""
    cells: dict[tuple, list[dict]] = {}
    for r in rows:
        cells.setdefault((r["scenario"], r["protocol"], r["network"]), []).append(r)

    out = {}
    for key, rs in sorted(cells.items()):
        scenario, protocol, network = key
        ok = [r for r in rs if r["handshake_ms"] is not None]
        ms = [r["handshake_ms"] for r in ok]
        ttf = [
            r["notes"].get("time_to_failure_ms")
            for r in rs
            if r["notes"].get("time_to_failure_ms")
        ]
        cell = {
            "n": len(rs),
            "outcomes": {},
            "round_trips": max((r["round_trips"] for r in rs), default=0),
            "round_trips_median": statistics.median(
                [r["round_trips"] for r in rs]
            ),
            "connections": statistics.median(
                [r["connections"] for r in rs if r["connections"] is not None]
            )
            if any(r["connections"] is not None for r in rs)
            else None,
            "retries_max": max((r["retries"] for r in rs), default=0),
            "validated_all": all(
                r["validated"] for r in rs if r["validated"] is not None
            )
            if any(r["validated"] is not None for r in rs)
            else None,
        }
        for r in rs:
            cell["outcomes"][r["outcome"]] = cell["outcomes"].get(r["outcome"], 0) + 1
        if ms:
            cell["median_ms"] = statistics.median(ms)
            lo, hi = bootstrap_ci(ms)
            cell["median_ci_lo"], cell["median_ci_hi"] = lo, hi
            cell["p95_ms"] = p95(ms)
            cell["min_ms"] = min(ms)
            cell["max_ms"] = max(ms)
        if ttf:
            cell["ttf_median_ms"] = statistics.median(ttf)
            cell["ttf_p95_ms"] = p95(ttf)
        # bytes (from successful handshakes)
        if ok:
            cell["wire_bytes_up"] = statistics.median(
                [r["wire_bytes_up"] for r in ok if r["wire_bytes_up"] is not None]
            )
            cell["wire_bytes_down"] = statistics.median(
                [r["wire_bytes_down"] for r in ok if r["wire_bytes_down"] is not None]
            )
            cell["app_bytes_up"] = statistics.median([r["app_bytes_up"] for r in ok])
            cell["app_bytes_down"] = statistics.median(
                [r["app_bytes_down"] for r in ok]
            )
            # phase decomposition: median of per-phase request time
            phases = {}
            for ph in ("discovery", "session", "capability", "readiness"):
                vals = [r["phases"].get(ph, {}).get("ms", 0.0) for r in ok]
                if any(v > 0 for v in vals):
                    phases[ph] = {
                        "median_ms": statistics.median(vals),
                        "round_trips": max(
                            r["phases"].get(ph, {}).get("round_trips", 0) for r in ok
                        ),
                    }
            cell["phases"] = phases
        # error info (from failed runs)
        errs = [r["error"] for r in rs if r["error"]]
        if errs:
            types = {}
            for e in errs:
                types[e["type"]] = types.get(e["type"], 0) + 1
            cell["error_types"] = types
            cell["error_example"] = errs[0]["message"][:200]
        out[f"{scenario}.{protocol}.{network}"] = cell
    return out
