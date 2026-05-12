import json
import re
import time
from typing import Dict, List, Optional

from ..core.logger import get_logger
from ..core.llm_json import parse_llm_json_object  # ✅ ADICIONAR ESTE IMPORT
from ..ports.ai_client import AIModelClientPort
from ..core.ast_skeleton import skeletonize_file  # 🆕 IMPORT SKELETONIZER
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

    def _analisar_bloco(
        self,
        caminho: str,
        bloco: str,
        work_items: List[dict] = None,
        skeleton: str = None
    ) -> tuple[Optional[dict], dict]:
        """Analyze a single code block using AI with robust JSON parsing.

        Uses parse_llm_json_object for resilient handling of malformed JSON
        from the AI model (includes json-repair fallback).
        """
        prompt = build_code_review_prompt(
            caminho=caminho,
            bloco=bloco,
            work_items=work_items,
            skeleton=skeleton
        )
        raw_json, usage = self._ai.complete(
            system_prompt=CODE_REVIEW_SYSTEM_PROMPT,
            user_prompt=prompt,
        )
        if raw_json:
            try:
                return parse_llm_json_object(raw_json, log_context="CodeReview"), usage
            except Exception as e:
                log.error("Failed to parse AI Code Review JSON: %s", str(e))
        return None, usage

    def analyze_pr_code(
        self,
        mapa_diffs: Dict[str, str],
        mapa_full: Dict[str, str] = None,
        work_items: List[dict] = None,
    ) -> tuple[Optional[dict], dict]:
        """Performs a comprehensive code review on a set of files.

        Combines static analysis and LLM-based analysis. Handles block splitting,
        token budgeting, and result aggregation (deduplication and scoring).

        Args:
            mapa_diffs (Dict[str, str]): A dictionary mapping file paths to their diff content.
            mapa_full (Dict[str, str]): Optional dictionary mapping paths to FULL content for skeletonization.
            work_items (List[dict]): Optional list of ACs/Work Items for requirements-guided review.

        Returns:
            tuple[Optional[dict], dict]: A tuple containing the review results
            (findings, score, approval) and execution metrics.
        """
        start_time = time.time()
        total_tokens = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_reasoning_tokens = 0
        all_f, all_p = [], []
        budget_exceeded = False

        # Pre-calculate skeletons to share across blocks
        skeletons = {}
        if mapa_full:
            for path, content in mapa_full.items():
                skeletons[path] = skeletonize_file(path, content)

        for path, content in mapa_diffs.items():
            static_findings = StaticAnalyzer.analyze_file(path, content)
            if static_findings:
                log.info("  [STATIC] '%s' — %d finding(s)", path, len(static_findings))
                all_f.extend(static_findings)

        for path, content in mapa_diffs.items():
            blocos = self._dividir_em_blocos(content)
            context_header = self._build_context_header(content)
            skeleton_context = skeletons.get(path)

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
                res, usage = self._analisar_bloco(
                    caminho=path,
                    bloco=context_header + b,
                    work_items=work_items,
                    skeleton=skeleton_context,
                )
                
                input_tok = usage.get("prompt_tokens", 0)
                output_tok = usage.get("completion_tokens", 0)
                reason_tok = usage.get("reasoning_tokens", 0)
                
                total_input_tokens += input_tok
                total_output_tokens += output_tok
                total_reasoning_tokens += reason_tok
                total_tokens = total_input_tokens + total_output_tokens
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
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "reasoning_tokens": total_reasoning_tokens,
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
