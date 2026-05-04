import json
import re
import time
from typing import Dict, List, Optional

from ..core.logger import get_logger
from ..core.llm_json import parse_llm_json_object  # ✅ ADICIONAR ESTE IMPORT
from ..ports.ai_client import AIModelClientPort
from .static_analyzer import StaticAnalyzer
from ..templates.prompts import build_code_review_prompt, CODE_REVIEW_SYSTEM_PROMPT

log = get_logger("CodeReview")


class CodeReviewService:
    """Orchestrates static and AI-based code analysis for Pull Requests.

    Attributes:
        _ai: Adapter for the AI model client.
        _max_high_block: Threshold for high-severity findings to block PR.
        _max_token_budget: Token consumption limit for AI analysis.
    """

    _SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    _SEVERITY_PENALTIES = {
        "critical": 3.0,
        "high": 1.5,
        "medium": 0.5,
        "low": 0.15,
    }

    def __init__(self, ai: AIModelClientPort, max_high_block: int, max_token_budget: int):
        """Initializes the CodeReviewService.

        Args:
            ai: The AI model client adapter.
            max_high_block: Max high-severity findings allowed before blocking.
            max_token_budget: Maximum tokens to consume per PR analysis.
        """
        self._ai = ai
        self._max_high_block = max_high_block
        self._max_token_budget = max_token_budget

    @staticmethod
    def _build_context_header(content: str) -> str:
        lines = content.splitlines()
        context = [l.strip() for l in lines if l.strip().startswith(("import ", "from ", "class ", "def "))]
        return "# FILE CONTEXT (imports & signatures):\n" + "\n".join(context[:30]) + "\n\n" if context else ""

    @staticmethod
    def _dividir_em_blocos(conteudo: str) -> List[str]:
        padrao = r"\n(?=\s*\d+\s*\|\s*(?:def |class |async def |public |private |protected |static |function ))"
        fragmentos = re.split(padrao, conteudo)
        blocos, atual = [], ""
        for i, frag in enumerate(fragmentos):
            atual += frag
            # Increased threshold from 200 to 400 to minimize API calls while maintaining context.
            if len(atual.splitlines()) >= 400 or i == len(fragmentos) - 1:
                if atual.strip():
                    blocos.append(atual)
                atual = ""
        return blocos if blocos else [conteudo]

    def _analisar_bloco(self, caminho: str, bloco: str) -> tuple[Optional[dict], int]:
        """Analyze a single code block using AI with robust JSON parsing.

        Uses parse_llm_json_object for resilient handling of malformed JSON
        from the AI model (includes json-repair fallback).
        """
        prompt = build_code_review_prompt(caminho, bloco)
        raw_json, tokens = self._ai.complete(
            system_prompt=CODE_REVIEW_SYSTEM_PROMPT,
            user_prompt=prompt,
        )
        if raw_json:
            try:
                return parse_llm_json_object(raw_json, log_context="CodeReview"), tokens
            except Exception as e:
                log.error("Failed to parse AI Code Review JSON: %s", str(e))
        return None, tokens

    def analyze_pr_code(self, mapa: Dict[str, str]) -> tuple[Optional[dict], dict]:
        """Performs a comprehensive code review on a set of files.

        Combines static analysis and LLM-based analysis. Handles block splitting,
        token budgeting, and result aggregation (deduplication and scoring).

        Args:
            mapa (Dict[str, str]): A dictionary mapping file paths to their diff content.

        Returns:
            tuple[Optional[dict], dict]: A tuple containing the review results
            (findings, score, approval) and execution metrics.
        """
        start_time = time.time()
        total_tokens = 0
        all_f, all_p = [], []
        budget_exceeded = False

        for path, content in mapa.items():
            static_findings = StaticAnalyzer.analyze_file(path, content)
            if static_findings:
                log.info("  [STATIC] '%s' — %d finding(s)", path, len(static_findings))
                all_f.extend(static_findings)

        for path, content in mapa.items():
            blocos = self._dividir_em_blocos(content)
            context_header = self._build_context_header(content)
            log.info("Analysing '%s' — %d block(s)", path, len(blocos))

            for i, b in enumerate(blocos):
                if total_tokens >= self._max_token_budget:
                    log.warning(
                        "Token budget (%s) reached at block %s/%s for '%s'; skipping remaining LLM blocks.",
                        self._max_token_budget,
                        i + 1,
                        len(blocos),
                        path,
                    )
                    budget_exceeded = True
                    break

                log.info("  [BLOCK %d/%d]", i + 1, len(blocos))
                res, tokens = self._analisar_bloco(path, context_header + b)
                total_tokens += tokens
                if not res:
                    continue

                for f in res.get("findings", []):
                    f["file"] = path
                    all_f.append(f)
                all_p.extend(res.get("positive_aspects", []))

            if budget_exceeded:
                break

        metrics = {
            "time": round(time.time() - start_time, 1),
            "tokens": total_tokens,
            "token_budget_exceeded": budget_exceeded,
        }

        if not all_f:
            return None, metrics

        vistos, unique_f = set(), []
        sorted_all_f = sorted(all_f, key=lambda x: 0 if x.get("_source") == "static" else 1)

        for f in sorted_all_f:
            line_group = (f.get("line") or 0) // 3 if f.get("line") is not None else 0
            key = (f.get("file"), line_group, f.get("type"))
            if key not in vistos:
                vistos.add(key)
                unique_f.append(f)

        unique_f.sort(key=lambda x: self._SEVERITY_ORDER.get(x.get("severity", "low"), 99))

        # Deterministic scoring: start at 10 and subtract penalties per finding
        total_penalty = sum(self._SEVERITY_PENALTIES.get(f.get("severity", "low"), 0) for f in unique_f)
        final_score = max(1, min(10, round(10 - total_penalty)))

        sevs_list = [f.get("severity") for f in unique_f]
        num_high = sevs_list.count("high")
        has_critical = "critical" in sevs_list
        
        # Approval logic: Block if any Critical, too many High, or Score < 7
        approved = not (has_critical or num_high >= self._max_high_block or final_score < 7)
        
        log.info("Code Review Score: %s/10 (Penalty: %.2f) -> Approved: %s", final_score, total_penalty, approved)

        return {
            "findings": unique_f,
            "security_score": final_score,
            "approve": approved,
            "positive_aspects": all_p,
        }, metrics