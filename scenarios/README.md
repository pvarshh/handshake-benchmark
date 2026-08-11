# Scenarios

Scenario definitions (S1 cold start, S2 warm repeat, S3 capability mismatch,
S4a/b/c degraded conditions) are code, not config: the cell table lives in
`harness/scenarios.py` (`CELLS`), which maps every scenario x protocol-variant
cell to its counterpart server, environment flags, warm-state priming, and
runner arguments. Run them via `python -m harness.run --scenario <name>`.
