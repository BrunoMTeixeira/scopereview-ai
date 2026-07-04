import json
import re
import time
import asyncio
from typing import Dict, List, Optional

from ..core.logger import get_logger
from ..core.llm_json import parse_llm_json_object  # ✅ ADICIONAR ESTE IMPORT
from ..ports.ai_client import AIModelClientPort
from ..core.ast_skeleton import skeletonize_file  # 🆕 IMPORT SKELETONIZER
from .static_analyzer import StaticAnalyzer
from ..templates.prompts import build_code_review_prompt, CODE_REVIEW_SYSTEM_PROMPT
from ..domain.code_verdict import CodeReviewVerdict

log = get_logger("CodeReview")


class CodeReviewService:
    """Service for analyzing code changes in Pull Requests using AI.

    Extracts potential issues and positive aspects from code diffs,
    delegating the final verdict logic to the CodeReviewVerdict domain.
    """

    def __init__(self, ai: AIModelClientPort, static_analyzer: StaticAnalyzer, max_high_block: int, max_token_budget: int):
        """Initializes the CodeReviewService.

        Args:
            ai: The AI model client adapter.
            static_analyzer: Injected static code analyzer service.
            max_high_block: Max high-severity findings allowed before blocking.
            max_token_budget: Maximum tokens to consume per PR analysis.
        """
        self._ai = ai
        self._static_analyzer = static_analyzer
        self._max_high_block = max_high_block
        self._max_token_budget = max_token_budget
        self._concurrency_limit = asyncio.Semaphore(5) # Limita a 5 blocos simultâneos para evitar rate limits

    @staticmethod
    def _build_context_header(content: str) -> str:
        lines = content.splitlines()
        context = []
        for l in lines:
            clean = l.split("|", 1)[-1] if "|" in l else l
            clean = re.sub(r"^[+\-\s]+", "", clean).strip()
            if clean.startswith(("import ", "from ", "class ", "def ")):
                context.append(clean)
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

    async def _analisar_bloco(
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
        raw_json, usage = await self._ai.complete(
            system_prompt=CODE_REVIEW_SYSTEM_PROMPT,
            user_prompt=prompt,
        )
        if raw_json:
            try:
                result = parse_llm_json_object(raw_json, log_context="CodeReview")
                result["_usage"] = usage
                return result, usage
            except Exception as e:
                log.error("Failed to parse AI Code Review JSON: %s", str(e))
        return None, usage

    async def analyze_pr_code(
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
        raw_results = []
        total_tokens = 0
        budget_exceeded = False

        # Pre-calculate skeletons to share across blocks
        skeletons = {}
        if mapa_full:
            for path, content in mapa_full.items():
                skeletons[path] = skeletonize_file(path, content)

        # 1. Static Analysis
        for path, content in mapa_diffs.items():
            static_findings = self._static_analyzer.analyze_file(path, content)
            if static_findings:
                log.info("  [STATIC] '%s' — %d finding(s)", path, len(static_findings))
                raw_results.append({"findings": static_findings, "_source": "static"})

        # 2. AI Analysis - Parallel Execution using gather
        tasks = []
        for path, content in mapa_diffs.items():
            blocos = self._dividir_em_blocos(content)
            context_header = self._build_context_header(content)
            skeleton_context = skeletons.get(path)

            log.info("Analysing '%s' — %d block(s)", path, len(blocos))

            for b in blocos:
                # Local wrapper function to apply semaphore and context mapping
                async def sem_task(c_path=path, c_bloco=context_header + b, c_skeleton=skeleton_context):
                    async with self._concurrency_limit:
                        res, usage = await self._analisar_bloco(
                            caminho=c_path,
                            bloco=c_bloco,
                            work_items=work_items,
                            skeleton=c_skeleton,
                        )
                        return c_path, res, usage

                tasks.append(sem_task())

        # Execute all AI block analyses concurrently
        task_results = await asyncio.gather(*tasks)

        # Process results
        for path, res, usage in task_results:
            if res:
                for f in res.get("findings", []):
                    f["file"] = path
                raw_results.append(res)
            
            total_tokens += usage.get("total_tokens", 0)
            if total_tokens >= self._max_token_budget:
                budget_exceeded = True

        # 3. Aggregate and evaluate verdict
        result, metrics = self._normalize_and_aggregate(raw_results, mapa_diffs)
        metrics["time"] = round(time.time() - start_time, 1)
        metrics["token_budget_exceeded"] = budget_exceeded
        
        return result, metrics

    def _normalize_and_aggregate(self, raw_results: List[dict], mapa_diffs: Dict[str, str]) -> tuple[dict, dict]:
        """Deduplicates findings across multiple files and calculates the verdict.

        Delegates business logic to the domain model (CodeReviewVerdict).
        """
        all_f = []
        all_p = []
        total_tokens = 0
        total_reasoning = 0
        total_input = 0
        total_output = 0

        for res in raw_results:
            if not res:
                continue
            all_f.extend(res.get("findings", []))
            all_p.extend(res.get("positive_aspects", []))
            usage = res.get("_usage", {})
            total_tokens += usage.get("total_tokens", 0)
            total_reasoning += usage.get("reasoning_tokens", 0)
            total_input += usage.get("prompt_tokens", 0)
            total_output += usage.get("completion_tokens", 0)

        # ── Finding Enrichment & Post-processing ──────────────────────────────
        # 1. Pre-compute a line cache from mapa_diffs for absolute code accuracy
        line_cache = {}
        for path, diff_content in mapa_diffs.items():
            file_cache = {}
            for d_line in diff_content.splitlines():
                if "|" in d_line:
                    # Robust regex mapping to resist | characters inside code itself
                    match = re.match(r"^[+\-\s]*(\d+)\s*\|(.*)", d_line)
                    if match:
                        num = int(match.group(1))
                        code_piece = match.group(2)
                        if code_piece.lstrip().startswith("-"):
                            continue
                        file_cache[num] = code_piece
            line_cache[path] = file_cache

        # 2. Iterate and enrich lazy/missing code snippets automatically
        for f in all_f:
            path = f.get("file")
            line = f.get("line")

            vuln_list = f.get("vulnerable_code") or []
            is_lazy = not vuln_list or all(str(line) in str(v).strip() and len(str(v).strip()) < 8 for v in vuln_list)

            if is_lazy and path in line_cache and line in line_cache[path]:
                f["vulnerable_code"] = [line_cache[path][line].rstrip()]
            elif vuln_list and not any("|" in str(v) for v in vuln_list) and path in line_cache and line in line_cache[path]:
                f["vulnerable_code"] = [line_cache[path][line].rstrip()]

        # Delegate business rules to the Domain Layer
        verdict_engine = CodeReviewVerdict(self._max_high_block)
        unique_f, final_score, approved = verdict_engine.evaluate(all_f)

        metrics = {
            "tokens": total_tokens,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "reasoning_tokens": total_reasoning,
        }

        return {
            "findings": unique_f,
            "security_score": final_score,
            "approve": approved,
            "positive_aspects": all_p,
        }, metrics
