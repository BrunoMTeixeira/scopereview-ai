import httpx
import time

from ..core.logger import get_logger
from ..domain.triage import triage_files, TriageLevel
from ..core.knowledge_ledger import build_ledger, format_ledger_for_prompt
from ..core.ast_skeleton import compress_map, extract_ac_terms
from ..ports.repository_client import RepositoryClientPort
from ..ports.dedup import PipelineDedupPort
from .code_review import CodeReviewService
from .requirements_review import RequirementsReviewService
from ..templates.markdown import format_code_review, format_requirements_review
from ..core.metrics import metrics
from ..domain.requirements_verdict import explain_must_gate_breach

log = get_logger("Orchestrator")


class PipelineOrchestrator:
    """Orchestrates the sequential code review and requirements validation pipeline.

    SEQUENTIAL ORCHESTRATION PATTERN (#43):
    This service coordinates the pipeline by strictly executing Agent 1 (Code Review)
    followed by Agent 2 (Requirements Validation). It ensures that deterministic findings
    (Shift-Left) and token budgets are passed between agents in a sequential handshake.

    TOKEN OPTIMIZATION STRATEGIES (active):
    1. Semantic Triage Gate — skips trivial files (docs, configs, locks) before LLM.
    2. Cross-Agent Knowledge Ledger — maps findings to NFRs, avoids duplicate analysis.
    3. Prompt Caching — Azure OpenAI auto-caches static system prompt prefixes (≥1024 tokens).
    """

    def __init__(
        self,
        ado: RepositoryClientPort,
        code_review: CodeReviewService,
        requirements_review: RequirementsReviewService,
        dedup: PipelineDedupPort,
        *,
        code_model_display_name: str,
        requirements_model_display_name: str,
    ):
        self._ado = ado
        self._code_review = code_review
        self._requirements_review = requirements_review
        self._dedup = dedup
        self._code_model_display_name = code_model_display_name
        self._requirements_model_display_name = requirements_model_display_name

    async def process_pr_pipeline(self, pr_id: int, repo_id: str, project: str) -> None:
        """Executes the complete pipeline for a Pull Request.

        1. Fetches PR details and changed files from ADO.
        2. Applies Semantic Triage Gate to filter trivial files.
        3. Runs the Code Review Agent (Phase 1 & 2) on non-trivial files.
        4. Builds Knowledge Ledger from findings (cross-agent dedup).
        5. Posts Code Review findings to the PR.
        6. Runs the Requirements Validation Agent (injecting findings + ledger).
        7. Posts Requirements Validation results to the PR.

        Args:
            pr_id (int): The numeric ID of the Pull Request.
            repo_id (str): The unique identifier for the repository.
            project (str): The name of the ADO project.

        Note:
            This method is intended to be run as a background task.
            It handles its own deduplication and error reporting.
        """

        if self._dedup.should_skip_duplicate(pr_id, "orchestrator"):
            log.info("PR #%s pipeline already in progress. Skipping duplicate.", pr_id)
            return

        try:
            start_time = time.time()
            log.info("Background task started: Starting orchestrated pipeline for PR #%s", pr_id)

            pr_info = await self._ado.get_pr_details(repo_id, pr_id, project)
            if not pr_info:
                log.error("Could not fetch PR details. Aborting pipeline.")
                return

            commit_sha = pr_info["commit_sha"]
            base_sha = pr_info.get("base_sha", "")
            mapa_full, mapa_diffs, total_eligible = await self._ado.get_changed_files(repo_id, pr_id, project, commit_sha, base_sha)

            if not mapa_full and not mapa_diffs:
                log.warning("No valid/supported files changed in PR #%s.", pr_id)
                await self._ado.post_pr_status(repo_id, pr_id, project, state="succeeded", description="No valid/supported files to review.")
                return

            if total_eligible > len(mapa_full):
                truncation_msg = f"⚠️ **File Limit Exceeded:** This Pull Request contains {total_eligible} supported files, but the configured limit (`MAX_FILES`) is {len(mapa_full)}. Only the first {len(mapa_full)} files were analyzed."
                await self._ado.post_comment(repo_id, pr_id, project, truncation_msg)
                log.warning("PR #%s truncated %d to %d files.", pr_id, total_eligible, len(mapa_full))

            # ── STRATEGY 1: Semantic Triage Gate ─────────────────────────────
            # Classify files into SKIP/LIGHT/FULL before any LLM invocation.
            # SKIP files (docs, configs, locks) are removed entirely from both maps.
            triage_buckets = triage_files(mapa_diffs)
            skipped_files = set(triage_buckets[TriageLevel.SKIP].keys())

            if skipped_files:
                log.info(
                    "Triage: Skipping %d trivial file(s) from LLM analysis: %s",
                    len(skipped_files),
                    list(skipped_files),
                )
                # Remove trivial files from both maps
                mapa_diffs = {k: v for k, v in mapa_diffs.items() if k not in skipped_files}
                mapa_full = {k: v for k, v in mapa_full.items() if k not in skipped_files}

            if not mapa_diffs:
                log.info("All files triaged as SKIP for PR #%s. No LLM analysis needed.", pr_id)
                await self._ado.post_pr_status(repo_id, pr_id, project, state="succeeded", description="All changed files are trivial (skipped).")
                return

            # PRE-LOAD REQUIREMENTS & RULES EARLY
            # Ensures Code Review Agent has visibility into Acceptance Criteria context
            # to detect functional regressions, stubs, and unimplemented requirements.
            log.info("Pre-loading PR Requirements & Repository Rules for early context injection...")
            work_items = await self._ado.get_work_items(repo_id, pr_id, project)
            regras_repo = await self._ado.get_repo_rules(repo_id, project, commit_sha)

            log.info("Running Code Review Agent (Guided by Requirements)...")
            # ENHANCED CODE REVIEW: Passing Diff (focus), Mapa Full (for skeleton generation),
            # and Work Items (for context-aware regression detection).
            cr_result, cr_metrics = await self._code_review.analyze_pr_code(
                mapa_diffs=mapa_diffs,
                mapa_full=mapa_full,
                work_items=work_items,
            )

            findings_to_inject = []
            ledger_context = None

            if cr_result:
                cr_markdown = format_code_review(
                    cr_result,
                    cr_metrics,
                    model_display_name=self._code_model_display_name,
                )
                await self._ado.post_comment(repo_id, pr_id, project, cr_markdown)

                # SHIFT-LEFT: Capture findings to influence the next phase (Requirements)
                findings_to_inject = [
                    f for f in cr_result.get("findings", []) if f.get("type") in ("quality", "bug", "security")
                ]

                # ── STRATEGY 2: Cross-Agent Knowledge Ledger ─────────────────
                # Map findings to known NFRs so Requirements Agent skips re-analysis.
                if findings_to_inject:
                    log.info(
                        "Shift-Left: Injecting %d findings into Requirements Validation context.",
                        len(findings_to_inject),
                    )
                    ledger = build_ledger(findings_to_inject)
                    ledger_context = format_ledger_for_prompt(ledger)
                    if ledger_context:
                        log.info(
                            "Knowledge Ledger: %d NFRs pre-verified, will skip re-analysis in Requirements Agent.",
                            len(ledger),
                        )
            else:
                log.info("Code Review returned no findings.")

            # CIRCUIT BREAKER: If Phase 1 exceeded budget, do not start Phase 2 (Cost Control)
            if cr_metrics.get("token_budget_exceeded"):
                log.warning(
                    "Circuit Breaker: Token budget reached in Phase 1. Skipping Requirements Validation for PR #%s.",
                    pr_id,
                )
                await self._ado.post_comment(
                    repo_id, pr_id, project,
                    "⚠️ **Circuit Breaker:** The configured token limit (`MAX_TOKEN_BUDGET`) was reached during Code Review. The **Requirements Validation phase was skipped** to control costs."
                )
                await self._ado.post_pr_status(repo_id, pr_id, project, state="succeeded", description="Token budget exceeded. Manual validation required.")
                return

            # ── STRATEGY 3: Prompt Caching (Azure OpenAI) ────────────────────
            # Azure OpenAI automatically caches identical prompt prefixes (≥1024 tokens)
            # at 50% cost. Our static REQUIREMENTS_SYSTEM_PROMPT is the cached prefix.
            # No code changes needed — this works out of the box.

            # Requirements already loaded at line 113
            log.info("Running Requirements Validation Agent...")

            # ── Finding Context Stripping ─────────────────────────────────────
            # The Requirements Agent only needs finding metadata (title, line, severity),
            # not the actual code snippets. This saves ~30 tokens per finding.
            lean_findings = [
                {k: v for k, v in f.items() if k not in ("vulnerable_code", "fixed_code", "suggestion_code")}
                for f in findings_to_inject
            ] if findings_to_inject else []

            # ── Code Compression ("AC-Aware Context Pruning") ─────────────────────────────
            # Dynamic AC-Aware Compression: extracts key terms from Acceptance
            # Criteria (quoted strings, UPPER_CASE, snake_case, numerics) and
            # keeps code lines matching those terms. Self-configuring for any domain.
            ac_terms = extract_ac_terms(work_items)
            mapa_compressed = compress_map(mapa_full, ac_terms=ac_terms)

            req_result, req_metrics = await self._requirements_review.validate_requirements(
                pr_info=pr_info,
                work_items=work_items,
                regras_repo=regras_repo,
                mapa_ficheiros=mapa_compressed,
                injected_findings=lean_findings,
                ledger_context=ledger_context,
            )

            if req_result:
                req_markdown = format_requirements_review(
                    req_result,
                    pr_info,
                    req_metrics,
                    model_display_name=self._requirements_model_display_name,
                )
                await self._ado.post_comment(repo_id, pr_id, project, req_markdown)
                log.info("Pipeline completed for PR #%s (code review + requirements).", pr_id)

                # Evaluate final verdict and send PR Status
                cr_approved = cr_result.get("approve", True) if cr_result else True

                req_list = req_result.get("requirements", []) if req_result else []
                must_breached, _ = explain_must_gate_breach(req_list)

                if not cr_approved or must_breached:
                    state = "failed"
                    desc = "ScopeReview AI found critical issues or MUST requirements that were not met."
                else:
                    state = "succeeded"
                    desc = "ScopeReview AI approved the Pull Request."

                await self._ado.post_pr_status(repo_id, pr_id, project, state=state, description=desc)

                # Record metrics
                t_in = cr_metrics.get("input_tokens", 0) + req_metrics.get("input_tokens", 0)
                t_out = cr_metrics.get("output_tokens", 0) + req_metrics.get("output_tokens", 0)
                total_tokens = t_in + t_out

                latency = (time.time() - start_time) * 1000  # ms
                metrics.record_analysis(
                    success=True,
                    total_tokens=total_tokens,
                    input_tokens=t_in,
                    output_tokens=t_out,
                    latency_ms=latency
                )
            else:
                log.error("Requirements Validation failed to generate a result.")
                await self._ado.post_pr_status(repo_id, pr_id, project, state="succeeded", description="Warning: Internal error during requirements validation (Fail-Open).")
                metrics.record_analysis(
                    success=False,
                    total_tokens=0,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=0
                )

        except httpx.RequestError as exc:
            log.error("Azure DevOps HTTP error for PR #%s: %s", pr_id, exc, exc_info=True)
            try:
                await self._ado.post_pr_status(repo_id, pr_id, project, state="succeeded", description="Análise indisponível por falha de rede (Fail-Open).")
            except Exception:
                pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.error("Pipeline data error for PR #%s: %s", pr_id, exc, exc_info=True)
            try:
                await self._ado.post_pr_status(repo_id, pr_id, project, state="succeeded", description="Análise indisponível por erro de formatação (Fail-Open).")
            except Exception:
                pass
        except Exception as exc:
            log.exception("Unhandled error in pipeline for PR #%s: %s", pr_id, exc)
            try:
                await self._ado.post_pr_status(repo_id, pr_id, project, state="succeeded", description="Análise indisponível por erro interno (Fail-Open).")
            except Exception:
                pass
        finally:
            # ESSENTIAL: Ensure the lock is always released to allow future runs
            self._dedup.release(pr_id, "orchestrator")
            log.debug("Dedup lock released for PR #%s", pr_id)
