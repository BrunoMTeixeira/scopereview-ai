"""
Canonical requirements verdict (MUST gate + status lattice).

Mirrors the contract described in `templates/prompts.py` so verdicts stay
consistent even when the model mis-labels `overall_verdict`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..core.logger import get_logger

log = get_logger("DomainPolicy")

_GAP_STATUSES = frozenset({"MISSING", "PARTIAL"})


def _norm_status(raw: Any) -> str:
    return (str(raw) if raw is not None else "").strip().upper()


def _norm_priority(raw: Any) -> str:
    return (str(raw) if raw is not None else "").strip().upper()


def compute_canonical_verdict(requirements: List[dict]) -> str:
    """
    Derive `overall_verdict` only from structured requirement rows.

    Rules (aligned with prompts):
    - NO_REQUIREMENTS — empty requirement list.
    - NEEDS_WORK — any MUST is MISSING/PARTIAL, or any requirement is MISSING/PARTIAL.
    - UNVERIFIABLE — every row is UNVERIFIABLE (and list non-empty).
    - APPROVED — no gaps; at least one IMPLEMENTED; any UNVERIFIABLE rows do not block
      when there is no MISSING/PARTIAL (verifiable items satisfied).
    """
    if not requirements:
        return "NO_REQUIREMENTS"

    statuses = [_norm_status(r.get("status")) for r in requirements]
    priorities = [_norm_priority(r.get("priority")) for r in requirements]

    for st, pr in zip(statuses, priorities):
        if pr == "MUST" and st in _GAP_STATUSES:
            return "NEEDS_WORK"

    if any(st in _GAP_STATUSES for st in statuses):
        return "NEEDS_WORK"

    if all(st == "UNVERIFIABLE" for st in statuses):
        return "UNVERIFIABLE"

    if all(st == "IMPLEMENTED" for st in statuses):
        return "APPROVED"

    if not any(st in _GAP_STATUSES for st in statuses) and any(st == "IMPLEMENTED" for st in statuses):
        return "APPROVED"

    return "UNVERIFIABLE"


def _reconcile_reason(
    *,
    llm_verdict: str,
    canonical: str,
    existing_reason: Optional[str],
) -> str:
    note = f"[Policy: verdict normalized to {canonical!r} (model reported {llm_verdict!r}).]"
    base = (existing_reason or "").strip()
    if not base:
        return note
    if note in base:
        return base
    return f"{base} {note}".strip()


def apply_domain_verdict_rules(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a shallow-copied result dict with `overall_verdict` / `verdict_reason`
    corrected when they disagree with `compute_canonical_verdict`.
    """
    reqs: List[dict] = list(result.get("requirements") or [])
    canonical = compute_canonical_verdict(reqs)
    llm_verdict = _norm_status(result.get("overall_verdict"))
    if not llm_verdict:
        llm_verdict = "UNVERIFIABLE"

    # Compare using uppercase tokens the LLM uses
    if llm_verdict == canonical:
        return dict(result)

    log.warning("VERDICT OVERRIDE: IA reported %s, but Domain Policy enforced %s", llm_verdict, canonical)
    out = dict(result)
    out["overall_verdict"] = canonical
    out["verdict_reason"] = _reconcile_reason(
        llm_verdict=llm_verdict,
        canonical=canonical,
        existing_reason=result.get("verdict_reason"),
    )
    return out


def explain_must_gate_breach(requirements: List[dict]) -> Tuple[bool, List[str]]:
    """Returns (breached, human-readable ids) for diagnostics / tests."""
    bad: List[str] = []
    for r in requirements:
        if _norm_priority(r.get("priority")) != "MUST":
            continue
        if _norm_status(r.get("status")) in _GAP_STATUSES:
            rid = str(r.get("id", "?"))
            bad.append(rid)
    return (len(bad) > 0, bad)
