import time
from typing import Dict, List, Optional

from pydantic import ValidationError

from ..core.config import settings
from ..core.llm_json import parse_llm_json_object
from ..core.logger import get_logger
from ..domain.requirements_verdict import apply_domain_verdict_rules
from ..models.ai_models import RequirementsResult
from ..ports.ai_client import AIModelClientPort
from ..templates.prompts import build_requirements_prompt, REQUIREMENTS_SYSTEM_PROMPT

log = get_logger("RequirementsReview")


class RequirementsReviewService:
    """Service for validating Pull Requests against functional and non-functional requirements.

    This service uses LLMs to compare code changes against Work Item acceptance
    criteria and repository rules. It ensures that the final verdict follows
    deterministic domain rules.
    """

    def __init__(self, ai: AIModelClientPort, max_completion_tokens: Optional[int] = None):
        """Initializes the RequirementsReviewService.

        Args:
            ai: The AI model client adapter.
            max_completion_tokens: Optional limit for LLM response length.
        """
        self._ai = ai
        self._max_completion_tokens = (
            max_completion_tokens
            if max_completion_tokens is not None
            else settings.REQUIREMENTS_MAX_COMPLETION_TOKENS
        )

    def validate_requirements(
        self,
        pr_info: dict,
        work_items: List[dict],
        regras_repo: str,
        mapa_ficheiros: Dict[str, str],
        injected_findings: Optional[list] = None,
    ) -> tuple[Optional[dict], dict]:
        """Validates the PR against extracted requirements and linked Work Items.

        Args:
            pr_info: Basic PR metadata (title, description, author).
            work_items: List of linked Work Items with AC.
            regras_repo: Global rules for the repository.
            mapa_ficheiros: Dictionary mapping file paths to full content.
            injected_findings: Optional findings from the Code Review phase.

        Returns:
            tuple[Optional[dict], dict]: A tuple containing the validation results
            (requirements status, verdict) and execution metrics.
        """
        start_time = time.time()

        prompt = build_requirements_prompt(
            pr_info=pr_info,
            work_items=work_items,
            regras_repo=regras_repo,
            mapa_ficheiros=mapa_ficheiros,
            injected_findings=injected_findings,
        )

        raw_json, tokens = self._ai.complete(
            system_prompt=REQUIREMENTS_SYSTEM_PROMPT,
            user_prompt=prompt,
            max_tokens=self._max_completion_tokens,
        )

        metrics = {
            "time": round(time.time() - start_time, 1),
            "tokens": tokens,
            "requirements_max_completion_tokens": self._max_completion_tokens,
        }

        if not raw_json:
            return None, metrics

        try:
            parsed = parse_llm_json_object(raw_json, log_context="requirements")
            resultado = RequirementsResult(**parsed)
            normalized = apply_domain_verdict_rules(resultado.model_dump())
            return normalized, metrics
        except ValidationError as err:
            log.error("Failed to validate AI Requirements structure: %s", err)
            # Debug: log the malformed JSON head and tail
            if raw_json and len(raw_json) > 400:
                log.warning(
                    "Requirements JSON head/tail (debug): head=%r ... tail=%r",
                    raw_json[:200],
                    raw_json[-200:],
                )
            return None, metrics
        except Exception as err:  # noqa: BLE001 — json repair / unexpected parse errors
            log.error("Failed to parse AI Requirements JSON: %s", err)
            if raw_json and len(raw_json) > 400:
                log.warning(
                    "Requirements JSON head/tail (debug): head=%r ... tail=%r",
                    raw_json[:200],
                    raw_json[-200:],
                )
            return None, metrics
