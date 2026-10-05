import json
import re
import time
import asyncio
from typing import Dict, List, Optional

from ..core.logger import get_logger
from ..core.llm_json import parse_llm_json_object  # âœ… ADICIONAR ESTE IMPORT
from ..ports.ai_client import AIModelClientPort
from ..core.ast_skeleton import skeletonize_file  # ðŸ†• IMPORT SKELETONIZER
from .static_analyzer import StaticAnalyzer
from ..templates.prompts import build_code_review_prompt, CODE_REVIEW_SYSTEM_PROMPT
from ..domain.code_verdict import CodeReviewVerdict
from ..core.config import settings

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
        self._concurrency_limit = asyncio.Semaphore(settings.MAX_BLOCK_CONCURRENCY)  # Limits to 5 concurrent blocks to avoid rate limits

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
    def _split_into_blocks(content: str) -> List[str]:
        pattern = r"\n(?=\s*\d+\s*\|\s*(?:def |class |async def |public |private |protected |static |function ))"
        fragments = re.split(pattern, content)
        blocks, current_block = [], ""
        for i, frag in enumerate(fragments):
            current_block += frag
            # Increased threshold from 200 to 400 to minimize API calls while maintaining context.
            if len(current_block.splitlines()) >= 400 or i == len(fragments) - 1:
                if current_block.strip():
                    blocks.append(current_block)
                current_block = ""
        return blocks if blocks else [content]

    async def _analyze_block(
        self,
        path: str,
        block: str,
        work_items: List[dict] = None,
        skeleton: str = None
    ) -> tuple[Optional[dict], dict]:
        """Analyze a single code block using AI with robust JSON parsing.

        Uses parse_llm_json_object for resilient handling of malformed JSON
        from the AI model (includes json-repair fallback).
        """
        prompt = build_code_review_prompt(
            path=path,
            block=block,
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
        diff_file_map: Dict[str, str],
        full_file_map: Dict[str, str] = None,
        work_items: List[dict] = None,
    ) -> tuple[Optional[dict], dict]:
        """Performs a comprehensive code review on a set of files.

        Combines static analysis and LLM-based analysis. Handles block splitting,
        token budgeting, and result aggregation (deduplication and scoring).

        Args:
            diff_file_map (Dict[str, str]): A dictionary mapping file paths to their diff content.
            full_file_map (Dict[str, str]): Optional dictionary mapping paths to FULL content for skeletonization.
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
        if full_file_map:
            for path, content in full_file_map.items():
                skeletons[path] = skeletonize_file(path, content)

        # 1. Static Analysis
        for path, content in diff_file_map.items():
            static_findings = self._static_analyzer.analyze_file(path, content)
            if static_findings:
                log.info("  [STATIC] '%s' â€” %d finding(s)", path, len(static_findings))
                raw_results.append({"findings": static_findings, "_source": "static"})

        # 2. AI Analysis - Parallel Execution using gather
        tasks = []
        for path, content in diff_file_map.items():
            blocks = self._split_into_blocks(content)
            context_header = self._build_context_header(content)
            skeleton_context = skeletons.get(path)

            log.info("Analysing '%s' â€” %d block(s)", path, len(blocks))

            for b in blocks:
                # Local wrapper function to apply semaphore and context mapping
                async def sem_task(c_path=path, c_block=context_header + b, c_skeleton=skeleton_context):
                    async with self._concurrency_limit:
                        res, usage = await self._analyze_block(
                            path=c_path,
                            block=c_block,
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
                findings_list = res.get("findings") or []
                for f in findings_list:
                    f["file"] = path
                raw_results.append(res)

            total_tokens += usage.get("total_tokens", 0)
            if total_tokens >= self._max_token_budget:
                budget_exceeded = True

        # 3. Aggregate and evaluate verdict
        result, metrics = self._normalize_and_aggregate(raw_results, diff_file_map)
        metrics["time"] = round(time.time() - start_time, 1)
        metrics["token_budget_exceeded"] = budget_exceeded

        return result, metrics

    def _normalize_and_aggregate(self, raw_results: List[dict], diff_file_map: Dict[str, str]) -> tuple[dict, dict]:
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
            all_f.extend(res.get("findings") or [])
            all_p.extend(res.get("positive_aspects") or [])
            usage = res.get("_usage", {})
            total_tokens += usage.get("total_tokens", 0)
            total_reasoning += usage.get("reasoning_tokens", 0)
            total_input += usage.get("prompt_tokens", 0)
            total_output += usage.get("completion_tokens", 0)

        # â”€â”€ Finding Enrichment & Post-processing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # 1. Pre-compute a line cache from diff_file_map for absolute code accuracy
        line_cache = {}
        for path, diff_content in diff_file_map.items():
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
        # 3. Evidence Bar enforcement
        # Guard A: import statements are NEVER security code-execution sinks.
        #          Any CRITICAL/HIGH security finding whose vulnerable_code is an import
        #          is reclassified as LOW quality.
        # Guard B: proof gate (LLM findings only) - CRITICAL/HIGH must carry a concrete exploit chain.
        #          taint_source + sink_line must each be >= 15 chars or finding downgrades to MEDIUM.
        _import_re = re.compile(r"^\s*[+\-]?\s*(import\s+\w+|from\s+\S+\s+import\s+)")
        for _f in all_f:
            _sev = (_f.get("severity") or "").lower()
            if _sev not in ("critical", "high"):
                continue

            # Guard A: import statements are never RCE sinks (applies to ALL findings)
            _vuln = _f.get("vulnerable_code") or []
            if _f.get("type") == "security" and any(_import_re.match(str(v)) for v in _vuln):
                log.info("EvidenceBar[import-guard] '%s' line=%s: %s->low/quality", _f.get("title"), _f.get("line"), _sev)
                _f["severity"] = "low"
                _f["type"] = "quality"
                continue

            # Guard B: proof gate - LLM findings only (static findings have deterministic rules)
            if _f.get("_source") == "static":
                continue

            _taint = (_f.get("taint_source") or "").strip()
            _sink = (_f.get("sink_line") or "").strip()
            if len(_taint) < 15 or len(_sink) < 15:
                log.info("EvidenceBar[proof-gate] '%s' line=%s: %s->medium (taint=%d, sink=%d chars)", _f.get("title"), _f.get("line"), _sev, len(_taint), len(_sink))
                _f["severity"] = "medium"


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
