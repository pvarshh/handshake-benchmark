"""Token accounting for handshake capability metadata.

Counts the tokens an LLM-driven initiator would ingest if it read the
counterpart's capability metadata during the handshake, using the canonical
wire bodies captured from real runs (agents/metadata/).

Tokenizer: if Anthropic API credentials resolve (ANTHROPIC_API_KEY or an
`ant auth login` profile), counts come from the free `count_tokens`
endpoint against the pricing model itself (exact, minus a calibrated
fixed message-envelope overhead). Otherwise falls back to tiktoken
o200k_base as an approximation; Anthropic's tokenizer counts ~15-20%
higher on typical JSON, so fallback dollar figures are a lower bound.
The method used is recorded in the output (`_method`) and stated in the
paper.

Pricing: Claude Sonnet 5 list price, $3.00 per million input tokens
(intro pricing through 2026-08-31 is lower; we use the list price).
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = os.path.join(ROOT, "agents", "metadata")

PRICE_PER_MTOK_USD = 3.00
PRICING_MODEL = "claude-sonnet-5"

# Metadata an LLM ingests during one cold handshake, per protocol variant
SOURCES = {
    "mcp-legacy": ["mcp_initialize_result.json", "mcp_tools_list_result.json"],
    "mcp-modern": ["mcp_discover_result.json", "mcp_tools_list_modern.json"],
    "a2a": ["a2a_card.json"],
    "acp": ["acp_manifest.json"],
}


def _make_counter():
    """Return (count_fn, method). Prefers the exact Anthropic tokenizer."""
    try:
        import anthropic

        client = anthropic.Anthropic()

        def raw(text: str) -> int:
            return client.messages.count_tokens(
                model=PRICING_MODEL,
                messages=[{"role": "user", "content": text}],
            ).input_tokens

        # count_tokens includes a small fixed per-message envelope; calibrate
        # it with a single-token body so we report the metadata body alone.
        envelope = raw("x") - 1  # also serves as the credential probe
        return (lambda text: max(0, raw(text) - envelope)), "anthropic-count-tokens"
    except Exception:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")
        return (lambda text: len(enc.encode(text))), "tiktoken-o200k_base"


def token_costs() -> dict:
    counter, method = _make_counter()
    out = {"_method": method, "_pricing_model": PRICING_MODEL}
    for protocol, files in SOURCES.items():
        total_tokens = 0
        total_bytes = 0
        for name in files:
            with open(os.path.join(META, name)) as f:
                body = f.read().strip()
            total_tokens += counter(body)
            total_bytes += len(body.encode())
        out[protocol] = {
            "tokens": total_tokens,
            "bytes": total_bytes,
            "usd_per_handshake": total_tokens * PRICE_PER_MTOK_USD / 1e6,
            "usd_per_million_handshakes": total_tokens * PRICE_PER_MTOK_USD,
        }
    return out
