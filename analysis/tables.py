"""LaTeX table generation: T1 (main results) and T2 (degraded conditions),
plus the token-cost table (F3's tabular form; no NL-negotiation variant)."""

from __future__ import annotations

PROTO_TEX = {
    "mcp-legacy": "MCP (legacy)",
    "mcp-modern": "MCP (modern)",
    "mcp-legacy-warm": "MCP (legacy)",
    "mcp-modern-warm": "MCP (modern, auto)",
    "mcp-pinned-warm": "MCP (modern, pinned)",
    "a2a": "A2A",
    "acp": "ACP",
    "a2a-warm": "A2A",
    "acp-warm": "ACP",
}


def _ms(v) -> str:
    if v is None:
        return "--"
    if v < 0.05:
        return "$<$0.1"
    if v < 100:
        return f"{v:.1f}"
    return f"{v:.0f}"


def _int(v) -> str:
    return "--" if v is None else f"{int(round(v))}"


def table1(summary: dict) -> str:
    """T1: S1-S3, all quantitative metrics."""
    scen_rows = [
        ("S1 cold start", "s1", ["mcp-legacy", "mcp-modern", "a2a", "acp"]),
        (
            "S2 warm repeat",
            "s2",
            [
                "mcp-legacy-warm",
                "mcp-modern-warm",
                "mcp-pinned-warm",
                "a2a-warm",
                "acp-warm",
            ],
        ),
        ("S3 mismatch", "s3", ["mcp-legacy", "mcp-modern", "a2a", "acp"]),
    ]
    lines = [
        r"\begin{tabular}{llrrrrrrrr}",
        r"\toprule",
        r" & & & & \multicolumn{2}{c}{localhost (ms)} & \multicolumn{2}{c}{50\,ms RTT (ms)} & \multicolumn{2}{c}{wire bytes} \\",
        r"\cmidrule(lr){5-6}\cmidrule(lr){7-8}\cmidrule(lr){9-10}",
        r"Scenario & Protocol & RT & Conn & med & p95 & med & p95 & up & down \\",
        r"\midrule",
    ]
    for title, scen, protos in scen_rows:
        for i, p in enumerate(protos):
            local = summary.get(f"{scen}.{p}.local", {})
            rtt = summary.get(f"{scen}.{p}.rtt50", {})
            first = title if i == 0 else ""
            lines.append(
                f"{first} & {PROTO_TEX[p]} & {_int(local.get('round_trips_median'))}"
                f" & {_int(local.get('connections'))}"
                f" & {_ms(local.get('median_ms'))} & {_ms(local.get('p95_ms'))}"
                f" & {_ms(rtt.get('median_ms'))} & {_ms(rtt.get('p95_ms'))}"
                f" & {_int(local.get('wire_bytes_up'))} & {_int(local.get('wire_bytes_down'))} \\\\"
            )
        if scen != "s3":
            lines.append(r"\addlinespace")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def table2(summary: dict) -> str:
    """T2: S4 degraded conditions - outcome, time, retries, error surface."""
    rows = [
        ("S4a slow (2\\,s/resp.)", "s4a", ["mcp-legacy", "mcp-modern", "a2a", "acp"], "local"),
        ("S4b truncated metadata", "s4b", ["mcp-legacy", "mcp-modern", "a2a", "acp"], "local"),
        ("S4c version mismatch", "s4c", ["mcp-legacy", "mcp-modern", "a2a"], "local"),
    ]
    lines = [
        r"\begin{tabular}{llllrl}",
        r"\toprule",
        r"Scenario & Protocol & Outcome & med.\ time (ms) & Retr. & Error surfaced \\",
        r"\midrule",
    ]
    for title, scen, protos, net in rows:
        for i, p in enumerate(protos):
            c = summary.get(f"{scen}.{p}.{net}", {})
            first = title if i == 0 else ""
            outcomes = c.get("outcomes", {})
            outcome = max(outcomes, key=outcomes.get) if outcomes else "--"
            t = c.get("median_ms") if outcome in ("ready", "rejected") else c.get(
                "ttf_median_ms"
            )
            if c.get("error_types"):
                err = max(c["error_types"], key=c["error_types"].get)
                err = r"\texttt{" + err.replace("_", r"\_") + "}"
            elif outcome == "ready" and scen == "s4c":
                err = "none (version deferred)"
            else:
                err = "none"
            lines.append(
                f"{first} & {PROTO_TEX[p]} & {outcome} & {_ms(t)} & "
                f"{_int(c.get('retries_max'))} & {err} \\\\"
            )
        if scen != "s4c":
            lines.append(r"\addlinespace")
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


def table_tokens(tok: dict) -> str:
    """Token/dollar cost of handshake capability metadata (F3, tabular)."""
    order = ["mcp-legacy", "mcp-modern", "a2a", "acp"]
    lines = [
        r"\begin{tabular}{lrrr}",
        r"\toprule",
        r"Protocol & metadata bytes & tokens & USD / $10^6$ handshakes \\",
        r"\midrule",
    ]
    for p in order:
        t = tok[p]
        lines.append(
            f"{PROTO_TEX[p]} & {t['bytes']} & {t['tokens']} & "
            f"\\${t['usd_per_million_handshakes']:.0f} \\\\"
        )
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)


INVPROT = {"mcp": "MCP", "a2a": "A2A", "acp": "ACP"}


def table_inventory(inv: dict) -> str:
    """Capability metadata cost as a function of inventory size."""
    sizes = inv["sizes"]
    lines = [
        r"\begin{tabular}{l" + "r" * (len(sizes) + 1) + "}",
        r"\toprule",
        " & ".join(["Protocol"] + [f"$n{{=}}{n}$" for n in sizes]
                   + [r"per cap."]) + r" \\",
        r"\midrule",
    ]
    for key, label in INVPROT.items():
        d = inv["protocols"][key]
        cells = [f"{r['tokens']:,}" for r in d["rows"]]
        lines.append(
            f"{label} & " + " & ".join(cells)
            + f" & {d['tokens_per_capability']:.0f}" + r" \\"
        )
    lines += [r"\bottomrule", r"\end{tabular}"]
    return "\n".join(lines)
