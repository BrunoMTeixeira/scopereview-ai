"""Domain rules: policy that must hold regardless of LLM wording."""

from .requirements_verdict import (
    apply_domain_verdict_rules,
    compute_canonical_verdict,
    explain_must_gate_breach,
)

__all__ = [
    "apply_domain_verdict_rules",
    "compute_canonical_verdict",
    "explain_must_gate_breach",
]
