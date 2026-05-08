"""Cross-Agent Knowledge Ledger — Eliminates duplicate analysis between agents.

Maps Code Review findings to known Non-Functional Requirements (NFRs),
so the Requirements Agent can skip re-verifying issues already detected
by the Static Analyzer or Code Review Agent.

This is an ORIGINAL contribution: existing literature treats review agents
as independent. The Ledger creates a shared state that deduplicates work.

Reference: Token Optimization Eureka (ScopeReview AI, 2025).
"""

import re
from typing import Dict, List, Optional

from ..core.logger import get_logger

log = get_logger("KnowledgeLedger")


# Mapping: finding title pattern → NFR ID + status
# Each entry maps a Code Review finding to its corresponding NFR
_FINDING_TO_NFR: List[Dict] = [
    {
        "pattern": re.compile(r"unused import", re.I),
        "nfr_id": "NF-01",
        "nfr_label": "Sem Imports Não Utilizados",
        "status": "PARTIAL",
    },
    {
        "pattern": re.compile(r"print\(\)|print.+instead of log", re.I),
        "nfr_id": "NF-02",
        "nfr_label": "Logging Consistente",
        "status": "PARTIAL",
    },
    {
        "pattern": re.compile(r"DEBUG.+log|log.+DEBUG", re.I),
        "nfr_id": "NF-03",
        "nfr_label": "Nível de Logging Apropriado",
        "status": "PARTIAL",
    },
    {
        "pattern": re.compile(r"broad.+except|except.+Exception", re.I),
        "nfr_id": "NF-05",
        "nfr_label": "Tratamento de Exceções Específico",
        "status": "PARTIAL",
    },
    {
        "pattern": re.compile(r"unreachable|dead code", re.I),
        "nfr_id": "NF-06",
        "nfr_label": "Sem Código Unreachable",
        "status": "PARTIAL",
    },
    {
        "pattern": re.compile(r"hardcoded.+secret|secret.+hardcod", re.I),
        "nfr_id": "SEC-01",
        "nfr_label": "Sem Segredos Hardcoded",
        "status": "PARTIAL",
    },
    {
        "pattern": re.compile(r"SQL.+inject|inject.+SQL", re.I),
        "nfr_id": "SEC-02",
        "nfr_label": "Sem SQL Injection",
        "status": "PARTIAL",
    },
    {
        "pattern": re.compile(r"PII.+log|log.+PII|sensitive.+log", re.I),
        "nfr_id": "SEC-03",
        "nfr_label": "PII não exposta em logs",
        "status": "PARTIAL",
    },
]


def build_ledger(findings: List[dict]) -> Dict[str, dict]:
    """Build a knowledge ledger from Code Review findings.

    Scans all findings and maps them to known NFRs. Each matched NFR
    is recorded with its status, evidence (file:line), and the finding title.

    Args:
        findings: List of Code Review findings (from static + LLM).

    Returns:
        Dictionary keyed by NFR ID, containing status, label, and evidence.
    """
    ledger: Dict[str, dict] = {}

    for finding in findings:
        title = finding.get("title", "")
        desc = finding.get("description", "") or finding.get("reason", "")
        combined = f"{title} {desc}"

        for mapping in _FINDING_TO_NFR:
            if mapping["pattern"].search(combined):
                nfr_id = mapping["nfr_id"]
                evidence = f"{finding.get('file', '?')}:{finding.get('line', '?')}"

                if nfr_id not in ledger:
                    ledger[nfr_id] = {
                        "nfr_id": nfr_id,
                        "label": mapping["nfr_label"],
                        "status": mapping["status"],
                        "evidence": [evidence],
                        "titles": [title],
                    }
                else:
                    ledger[nfr_id]["evidence"].append(evidence)
                    ledger[nfr_id]["titles"].append(title)

    if ledger:
        log.info(
            "Knowledge Ledger: %d NFRs pre-verified from %d findings",
            len(ledger), len(findings),
        )
    return ledger


def format_ledger_for_prompt(ledger: Dict[str, dict]) -> Optional[str]:
    """Format the ledger as a compact prompt section for the Requirements Agent.

    Args:
        ledger: The knowledge ledger from build_ledger().

    Returns:
        Formatted string to inject into the Requirements prompt, or None if empty.
    """
    if not ledger:
        return None

    lines = [
        "=== PRE-VERIFIED NFRs (from Code Review Agent — do NOT re-analyze) ===",
        "The following non-functional requirements were already verified by the",
        "Code Review Agent. Accept these as source of truth. Focus your analysis",
        "ONLY on functional ACs and any NFRs NOT listed below.",
        "",
    ]

    for nfr_id, info in sorted(ledger.items()):
        evidence_str = ", ".join(info["evidence"][:3])
        lines.append(
            f"- {nfr_id} ({info['label']}): {info['status']} — "
            f"Evidence: {evidence_str}"
        )

    lines.append("")
    return "\n".join(lines)
