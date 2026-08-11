"""Capability-inventory sweep: how handshake metadata scales with the number
of capabilities a counterpart exposes.

The main experiment uses a one-capability counterpart, which makes the
measured wire and token costs protocol floors. That leaves an obvious
question unanswered: at what inventory size does capability metadata stop
being negligible? This module answers it directly.

Method: take the canonical wire body captured from a real run
(agents/metadata/), extract its single capability entry, and replicate that
entry N times with distinct names, keeping the envelope byte-identical. The
result is the exact document a counterpart with N such capabilities would
serve, so the byte counts are exact rather than extrapolated. Tokens are
counted with the same counter the headline table uses.

Names are padded to the width of the largest index so every entry costs the
same, and the resulting curve is linear in N by construction; what the sweep
measures is the per-capability slope and the intercept, which differ per
protocol because the envelopes differ.
"""

from __future__ import annotations

import copy
import json
import os

from analysis.tokens import META, _make_counter

SIZES = (1, 10, 50, 200)


def _mcp_tools(n: int) -> dict:
    doc = json.load(open(os.path.join(META, "mcp_tools_list_result.json")))
    proto = doc["result"]["tools"][0]
    doc["result"]["tools"] = [_named_tool(proto, i, n) for i in range(n)]
    return doc


def _named_tool(proto: dict, i: int, n: int) -> dict:
    t = copy.deepcopy(proto)
    name = _name(i, n)
    t["name"] = name
    t["inputSchema"]["title"] = f"{name}Arguments"
    t["outputSchema"]["title"] = f"{name}Output"
    return t


def _a2a_card(n: int) -> dict:
    doc = json.load(open(os.path.join(META, "a2a_card.json")))
    proto = doc["skills"][0]
    skills = []
    for i in range(n):
        s = copy.deepcopy(proto)
        s["id"] = _name(i, n)
        s["name"] = _name(i, n).capitalize()
        skills.append(s)
    doc["skills"] = skills
    return doc


def _acp_manifest(n: int) -> dict:
    doc = json.load(open(os.path.join(META, "acp_manifest.json")))
    proto = doc["agents"][0]
    agents = []
    for i in range(n):
        a = copy.deepcopy(proto)
        a["name"] = _name(i, n)
        agents.append(a)
    doc["agents"] = agents
    return doc


def _name(i: int, n: int) -> str:
    """Fixed-width names so every entry costs the same bytes."""
    width = len(str(n - 1)) if n > 1 else 1
    return f"echo{i:0{width}d}"


BUILDERS = {
    "mcp": _mcp_tools,
    "a2a": _a2a_card,
    "acp": _acp_manifest,
}


def sweep() -> dict:
    count, method = _make_counter()
    out: dict = {"_method": method, "sizes": list(SIZES), "protocols": {}}
    for proto, build in BUILDERS.items():
        rows = []
        for n in SIZES:
            body = json.dumps(build(n), separators=(",", ":"))
            rows.append({"n": n, "bytes": len(body), "tokens": count(body)})
        per_cap = (rows[-1]["tokens"] - rows[0]["tokens"]) / (SIZES[-1] - SIZES[0])
        out["protocols"][proto] = {"rows": rows, "tokens_per_capability": per_cap}
    return out


if __name__ == "__main__":
    print(json.dumps(sweep(), indent=2))
