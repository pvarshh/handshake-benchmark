"""Figures F1, F2, F4 (matplotlib, print-oriented PDFs for LaTeX).

Palette: validated colorblind-safe categorical slots (adjacent-pair CVD
dE >= 8, normal-vision dE >= 15; low-contrast slots carry direct value
labels per the relief rule). Color follows the protocol entity across all
figures.
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Figures are generated at final size so fonts land at their intended point
# size with no LaTeX rescaling; scaled-down text is the classic tell of a
# rushed reformat. These are the wsstyle measures: 5.5in text block, and a
# half-width slot for the two line charts that sit side by side in the text.
COL_W = 3.40
FULL_W = 5.50

PROTO_COLORS = {
    "mcp-legacy": "#2a78d6",
    "mcp-modern": "#eb6834",
    "a2a": "#1baf7a",
    "acp": "#eda100",
    "mcp-pinned": "#e87ba4",
}
PROTO_LABELS = {
    "mcp-legacy": "MCP (legacy)",
    "mcp-modern": "MCP (modern)",
    "a2a": "A2A",
    "acp": "ACP",
    "mcp-pinned": "MCP (pinned)",
}
PHASE_COLORS = {
    "discovery": "#1baf7a",
    "session": "#2a78d6",
    "capability": "#eb6834",
    "readiness": "#eda100",
    "processing": "#b9b7ae",
}

plt.rcParams.update(
    {
        "font.size": 9,
        "axes.titlesize": 9.5,
        "axes.labelsize": 9,
        "legend.fontsize": 8,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 150,
    }
)


def _bar_label(ax, rect, text, small=False):
    ax.annotate(
        text,
        xy=(rect.get_x() + rect.get_width() / 2, rect.get_height()),
        xytext=(0, 2),
        textcoords="offset points",
        ha="center",
        va="bottom",
        fontsize=7.5 if small else 8.5,
        color="#333",
    )


def fig1_round_trips(summary: dict, out: str):
    """F1: round trips to task-ready (or rejection), grouped by scenario."""
    groups = [
        ("S1 cold start", "s1", ["mcp-legacy", "mcp-modern", "a2a", "acp"], ""),
        (
            "S2 warm repeat",
            "s2",
            ["mcp-legacy-warm", "mcp-modern-warm", "mcp-pinned-warm", "a2a-warm", "acp-warm"],
            "-warm",
        ),
        ("S3 mismatch\n(to rejection)", "s3", ["mcp-legacy", "mcp-modern", "a2a", "acp"], ""),
    ]
    fig, ax = plt.subplots(figsize=(FULL_W, 2.5))
    x = 0.0
    xticks, xticklabels = [], []
    for title, scen, protos, suffix in groups:
        start = x
        for p in protos:
            base = p.replace("-warm", "").replace("mcp-pinned", "mcp-pinned")
            key = f"{scen}.{p}.local"
            cell = summary.get(key)
            if cell is None:
                continue
            rt = cell["round_trips_median"]
            color = PROTO_COLORS[base if base in PROTO_COLORS else p]
            if rt == 0:
                # zero-height bars get a colored baseline tick so the value
                # label still attributes to a protocol
                rect = ax.bar(x, 0.05, width=0.8, color=color)[0]
                _bar_label(ax, rect, "0")
            else:
                rect = ax.bar(x, rt, width=0.8, color=color)[0]
                _bar_label(ax, rect, f"{int(rt)}")
            x += 1.0
        xticks.append((start + x - 1.0) / 2)
        xticklabels.append(title)
        x += 0.9
    ax.set_xticks(xticks)
    ax.set_xticklabels(xticklabels)
    ax.set_ylabel("application-layer round trips")
    ax.set_ylim(0, 3.6)
    ax.set_yticks([0, 1, 2, 3])
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=PROTO_COLORS[p]) for p in PROTO_COLORS
    ]
    ax.legend(
        handles,
        [PROTO_LABELS[p] for p in PROTO_COLORS],
        ncol=5,
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.22),
        handlelength=1.0,
        columnspacing=1.0,
    )
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def fig2_phase_latency(summary: dict, out: str):
    """F2: S1 handshake latency decomposed by phase, median stacked bars with
    p95 whisker on the total; localhost and 50ms RTT panels."""
    protos = ["mcp-legacy", "mcp-modern", "a2a", "acp"]
    networks = [("local", "localhost"), ("rtt50", "emulated 50 ms RTT")]
    fig, axes = plt.subplots(1, 2, figsize=(FULL_W, 2.5))
    for ax, (net, title) in zip(axes, networks):
        for i, p in enumerate(protos):
            cell = summary.get(f"s1.{p}.{net}")
            if not cell:
                continue
            bottom = 0.0
            phase_sum = 0.0
            for ph in ("discovery", "session", "capability", "readiness"):
                v = cell.get("phases", {}).get(ph, {}).get("median_ms", 0.0)
                if v <= 0:
                    continue
                ax.bar(i, v, width=0.62, bottom=bottom, color=PHASE_COLORS[ph])
                bottom += v
                phase_sum += v
            residual = max(0.0, cell["median_ms"] - phase_sum)
            ax.bar(i, residual, width=0.62, bottom=bottom, color=PHASE_COLORS["processing"])
            total = cell["median_ms"]
            ax.plot(
                [i, i], [total, cell["p95_ms"]], color="#333", lw=0.9, solid_capstyle="butt"
            )
            ax.plot([i - 0.13, i + 0.13], [cell["p95_ms"]] * 2, color="#333", lw=0.9)
            ax.annotate(
                f"{total:.0f}" if total >= 10 else f"{total:.1f}",
                xy=(i + 0.36, total),
                fontsize=7.5,
                va="center",
                color="#333",
            )
        ax.set_xticks(range(len(protos)))
        ax.set_xticklabels(
            [PROTO_LABELS[p].replace(" (", "\n(") for p in protos], fontsize=7.5
        )
        ax.set_title(title)
    axes[0].set_ylabel("handshake latency (ms)")
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=PHASE_COLORS[k]) for k in PHASE_COLORS
    ]
    labels = ["discovery", "session", "capability", "readiness", "client processing"]
    fig.legend(
        handles,
        labels,
        ncol=5,
        frameon=False,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.06),
        handlelength=1.0,
        columnspacing=1.0,
    )
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def fig4_amortization(summary: dict, out: str):
    """F4: amortized per-interaction handshake cost over k interactions
    (cold + (k-1) x warm) / k, under emulated 50 ms RTT."""
    pairs = [
        ("mcp-legacy", "s1.mcp-legacy.rtt50", "s2.mcp-legacy-warm.rtt50"),
        ("mcp-modern", "s1.mcp-modern.rtt50", "s2.mcp-modern-warm.rtt50"),
        ("mcp-pinned", "s1.mcp-modern.rtt50", "s2.mcp-pinned-warm.rtt50"),
        ("a2a", "s1.a2a.rtt50", "s2.a2a-warm.rtt50"),
        ("acp", "s1.acp.rtt50", "s2.acp-warm.rtt50"),
    ]
    ks = [1, 2, 3, 5, 7, 10, 15, 20, 30, 50, 70, 100]
    fig, ax = plt.subplots(figsize=(COL_W, 2.45))
    ends = []
    ymax = 0.0
    for proto, cold_key, warm_key in pairs:
        cold = summary.get(cold_key, {}).get("median_ms")
        warm = summary.get(warm_key, {}).get("median_ms")
        if cold is None or warm is None:
            continue
        ys = [(cold + (k - 1) * warm) / k for k in ks]
        ymax = max(ymax, ys[0])
        ax.plot(ks, ys, color=PROTO_COLORS[proto], lw=1.6)
        ends.append((ys[-1], proto))
    # dodge right-edge direct labels so converging lines stay readable
    min_sep = ymax * 0.075
    ends.sort()
    label_y = []
    for y, proto in ends:
        ly = y if not label_y else max(y, label_y[-1] + min_sep)
        label_y.append(ly)
    for (y, proto), ly in zip(ends, label_y):
        # leader lines are annotation, not data: light gray, dashed, thin
        ax.plot(
            [100, 112],
            [y, ly],
            color="#bbbbbb",
            lw=0.6,
            ls=(0, (2, 2)),
            clip_on=False,
        )
        ax.annotate(
            PROTO_LABELS[proto],
            xy=(120, ly),
            xycoords="data",
            fontsize=6.5,
            va="center",
            color="#333",
            annotation_clip=False,
        )
    ax.set_xscale("log")
    ax.set_xticks([1, 3, 10, 30, 100])
    ax.set_xticklabels(["1", "3", "10", "30", "100"])
    ax.set_xlabel("interactions with the same counterpart (k)")
    ax.set_ylabel("amortized handshake ms / interaction")
    ax.set_xlim(1, 1000)
    ax.grid(axis="y", lw=0.4, color="#ddd")
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)


def fig5_rtt_sweep(sweep_rows: list[dict], out: str):
    """S1 latency-to-ready vs emulated RTT (median, N=20/cell): slope tracks
    the number of delayed exchanges per handshake."""
    import statistics

    protos = ["mcp-legacy", "mcp-modern", "a2a", "acp"]
    rtts = sorted({r["rtt_ms"] for r in sweep_rows})
    fig, ax = plt.subplots(figsize=(COL_W, 2.45))
    ends = []
    for p in protos:
        ys = []
        for rtt in rtts:
            ms = [
                r["handshake_ms"]
                for r in sweep_rows
                if r["protocol"] == p and r["rtt_ms"] == rtt
                and r["handshake_ms"] is not None
            ]
            ys.append(statistics.median(ms))
        ax.plot(rtts, ys, color=PROTO_COLORS[p], lw=1.6, marker="o", ms=3.5)
        ends.append((ys[-1], p))
    min_sep = max(y for y, _ in ends) * 0.06
    ends.sort()
    label_y = []
    for y, p in ends:
        ly = y if not label_y else max(y, label_y[-1] + min_sep)
        label_y.append(ly)
    for (y, p), ly in zip(ends, label_y):
        ax.plot([rtts[-1], rtts[-1] + 9], [y, ly], color="#bbbbbb", lw=0.6,
                ls=(0, (2, 2)), clip_on=False)
        ax.annotate(PROTO_LABELS[p], xy=(rtts[-1] + 11, ly), xycoords="data",
                    fontsize=6.5, va="center", color="#333",
                    annotation_clip=False)
    ax.set_xlabel("emulated RTT (ms)")
    ax.set_ylabel("median ms to task-ready")
    ax.set_xticks(rtts)
    ax.set_xlim(-4, rtts[-1] + 52)
    ax.grid(axis="y", lw=0.4, color="#ddd")
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
