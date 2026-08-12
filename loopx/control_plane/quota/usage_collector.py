"""Provider-neutral usage capture seam for per-goal token/cost/duration.

This module is the capture layer described in
``docs/architecture/rfcs/goal-usage-token-cost-v0.md``. It defines the
normalized, public-safe per-run usage shape and two sides of the seam:

- ``collect_usage_for_run`` reads a normalized ``usage`` block off a run dict
  for the aggregate loop in ``usage_summary``. It is provider-agnostic and
  performs no file IO.
- ``extract_codex_session_usage`` is the Codex ingestion helper: it reads a
  Codex session directory and returns the final cumulative token figure,
  reusing the extraction approach proven on the benchmark path.

Only aggregate numeric usage lives here. Prompts, completions, tool output,
credentials, and anything that reconstructs a conversation are never captured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class UsageSample:
    """Normalized, public-safe per-run LLM usage.

    Every field is aggregate numeric metadata. ``cost_usd`` and
    ``duration_ms`` may be zero when a runtime cannot measure them; the caller
    (the ingestion side) is responsible for filling them when the model id and
    wall-time are known.
    """

    input_tokens: int
    output_tokens: int
    cache_tokens: int
    cost_usd: float
    duration_ms: int
    provider: str
    model: str


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        coerced = int(value)
        return coerced if coerced >= 0 else None
    return None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        coerced = float(value)
        return coerced if coerced >= 0 else None
    return None


def collect_usage_for_run(run: dict[str, Any]) -> UsageSample | None:
    """Return normalized usage for a run, or ``None`` if the run reports none.

    Reads an optional ``usage`` block on the run dict. Runtimes that have not
    yet been wired to report usage contribute nothing, so the aggregate
    degrades gracefully to the prior run-history behavior.
    """
    usage = run.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = _coerce_int(usage.get("input_tokens"))
    output_tokens = _coerce_int(usage.get("output_tokens"))
    # A run claiming zero input and zero output has nothing aggregate-worthy.
    if input_tokens is None and output_tokens is None:
        return None
    return UsageSample(
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        cache_tokens=_coerce_int(usage.get("cache_tokens")) or 0,
        cost_usd=_coerce_float(usage.get("cost_usd")) or 0.0,
        duration_ms=_coerce_int(usage.get("duration_ms")) or 0,
        provider=str(usage.get("provider") or "unknown"),
        model=str(usage.get("model") or "unknown"),
    )


def extract_codex_session_usage(session_dir: Path) -> UsageSample | None:
    """Extract the final cumulative token count from a Codex session dir.

    Reuses the benchmark extraction approach: scan the session JSONL files for
    the last ``token_count`` event and read its cumulative ``total_token_usage``
    figure. Cost and wall-time are not present in the session log; the caller
    fills them from pricing and run wall-time when assembling the run record.
    """
    latest_input: int | None = None
    latest_output: int | None = None
    latest_cache = 0
    for session_file in sorted(session_dir.glob("*.jsonl")):
        try:
            lines = session_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "event_msg":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            total = info.get("total_token_usage")
            if not isinstance(total, dict):
                continue
            input_tokens = _coerce_int(total.get("input_tokens"))
            output_tokens = _coerce_int(total.get("output_tokens"))
            if input_tokens is None or output_tokens is None:
                continue
            latest_input = input_tokens
            latest_output = output_tokens
            cache_tokens = _coerce_int(total.get("cached_input_tokens"))
            if cache_tokens is not None:
                latest_cache = cache_tokens
    if latest_input is None or latest_output is None:
        return None
    return UsageSample(
        input_tokens=latest_input,
        output_tokens=latest_output,
        cache_tokens=latest_cache,
        cost_usd=0.0,
        duration_ms=0,
        provider="codex",
        model="codex",
    )
