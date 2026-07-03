from typing import Dict, Any, Tuple, List

from ..core.logger import get_logger

log = get_logger("CodeVerdict")

class CodeReviewVerdict:
    """Domain model defining the business rules for a Code Review result.
    
    Encapsulates severity weights, deduplication of findings by line group,
    mathematical score calculation, and the final approval policy.
    """

    _SEVERITY_PENALTIES = {
        "low": 0.5,
        "medium": 1.5,
        "high": 3.0,
        "critical": 5.0,
    }

    _SEVERITY_ORDER = {
        "critical": 1,
        "high": 2,
        "medium": 3,
        "low": 4,
    }

    def __init__(self, max_high_block: int):
        self._max_high_block = max_high_block

    def evaluate(self, raw_findings: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int, bool]:
        """Evaluates raw findings from the LLM and calculates the deterministic domain verdict.

        Args:
            raw_findings: List of finding dictionaries extracted from LLM.

        Returns:
            Tuple containing:
            - unique_findings: Deduplicated and sorted findings.
            - security_score: Mathematical score from 1 to 10.
            - approved: Boolean indicating if the PR passes the business rules.
        """
        vistos = set()
        unique_findings = []

        for f in raw_findings:
            # Group lines to avoid duplicate findings on adjacent lines
            line_group = (f.get("line") or 0) // 1 if f.get("line") is not None else 0
            key = (f.get("file"), line_group, f.get("type"))
            if key not in vistos:
                vistos.add(key)
                unique_findings.append(f)

        unique_findings.sort(key=lambda x: self._SEVERITY_ORDER.get(x.get("severity", "low"), 99))

        # Deterministic scoring: start at 10 and subtract penalties per finding
        total_penalty = sum(self._SEVERITY_PENALTIES.get(f.get("severity", "low"), 0) for f in unique_findings)
        final_score = max(1, min(10, round(10 - total_penalty)))

        sevs_list = [f.get("severity") for f in unique_findings]
        num_high = sevs_list.count("high")
        has_critical = "critical" in sevs_list

        # Approval logic: Block if any Critical, too many High, or Score < 7
        approved = not (has_critical or num_high >= self._max_high_block or final_score < 7)

        log.info("Code Review Score: %s/10 (Penalty: %.2f) -> Approved: %s", final_score, total_penalty, approved)

        return unique_findings, final_score, approved
