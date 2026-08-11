"""Run the full analysis: summaries, figures, tables, numbers.tex.

  python -m analysis.run_all [--raw results/raw] [--paper paper]
"""

from __future__ import annotations

import argparse
import json
import os

from analysis.figures import (
    fig1_round_trips,
    fig2_phase_latency,
    fig4_amortization,
    fig5_rtt_sweep,
)
from analysis.inventory import sweep as inventory_sweep
from analysis.load import load_baseline, load_rows, summarize
from analysis.numbers import emit
from analysis.tables import table1, table2, table_inventory, table_tokens
from analysis.tokens import token_costs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="results/raw")
    ap.add_argument("--paper", default="paper")
    args = ap.parse_args()

    rows = load_rows(args.raw)
    summary = summarize(rows)
    baseline = load_baseline(args.raw)
    tok = token_costs()
    inventory = inventory_sweep()
    n_runs = max((c["n"] for c in summary.values()), default=0)

    os.makedirs("results", exist_ok=True)
    with open("results/summary.json", "w") as f:
        json.dump({"cells": summary, "baseline": baseline, "tokens": tok,
                   "inventory": inventory}, f, indent=2)

    sweep_path = os.path.join(args.raw, "sweep.jsonl")
    sweep_rows = []
    if os.path.exists(sweep_path):
        with open(sweep_path) as f:
            sweep_rows = [json.loads(line) for line in f]

    figdir = os.path.join(args.paper, "figures")
    os.makedirs(figdir, exist_ok=True)
    fig1_round_trips(summary, os.path.join(figdir, "f1_round_trips.pdf"))
    fig2_phase_latency(summary, os.path.join(figdir, "f2_phase_latency.pdf"))
    fig4_amortization(summary, os.path.join(figdir, "f4_amortization.pdf"))
    if sweep_rows:
        fig5_rtt_sweep(sweep_rows, os.path.join(figdir, "f5_rtt_sweep.pdf"))

    tabdir = os.path.join(args.paper, "tables")
    os.makedirs(tabdir, exist_ok=True)
    with open(os.path.join(tabdir, "t1_main.tex"), "w") as f:
        f.write(table1(summary))
    with open(os.path.join(tabdir, "t2_degraded.tex"), "w") as f:
        f.write(table2(summary))
    with open(os.path.join(tabdir, "t3_tokens.tex"), "w") as f:
        f.write(table_tokens(tok))
    with open(os.path.join(tabdir, "t4_inventory.tex"), "w") as f:
        f.write(table_inventory(inventory))

    with open(os.path.join(args.paper, "numbers.tex"), "w") as f:
        f.write(emit(summary, tok, baseline, n_runs, sweep_rows, inventory))

    print(f"cells: {len(summary)}, runs: {len(rows)}")
    for k in sorted(summary):
        c = summary[k]
        med = c.get("median_ms")
        print(f"  {k}: n={c['n']} rt={c['round_trips_median']} med={med if med is None else round(med,1)}ms outcomes={c['outcomes']}")


if __name__ == "__main__":
    main()
