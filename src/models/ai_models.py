from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Code Review Models ───────────────────────────────────────────────────────


class CodeFinding(BaseModel):
    """Contract aligned with `templates/prompts.py` (LLM output).

    Accepts both compact schema (reason, fix) and legacy keys
    (justification, recommendation, description) for backward
    compatibility with the static analyzer.
    """

    model_config = ConfigDict(extra="allow")

    title: str = ""
    description: str = ""  # legacy — kept for static analyzer compat
    severity: str = "low"
    type: str = "quality"
    file: str = ""
    line: Optional[int] = None
    vulnerable_code: List[str] = Field(default_factory=list)
    # New compact keys (LLM output)
    reason: str = ""
    fix: str = ""
    # Legacy keys (static analyzer / old LLM responses)
    recommendation: str = ""
    fixed_code: List[str] = Field(default_factory=list)
    justification: str = ""
    suggestion_code: List[str] = Field(default_factory=list)

    @field_validator("vulnerable_code", "fixed_code", "suggestion_code", mode="before")
    @classmethod
    def _coerce_code_lists(cls, v: object) -> list:
        return v if v is not None else []


class CodeReviewResult(BaseModel):
    findings: List[CodeFinding]


# ─── Requirements Review Models ───────────────────────────────────────────────


class WorkItemAnalysed(BaseModel):
    id: int
    title: str
    type: str
    has_acceptance_criteria: bool
    priority: Optional[str] = None


class Requirement(BaseModel):
    id: str
    work_item_id: Optional[int] = None
    source: str = ""
    description: str = ""
    status: str = ""
    priority: Optional[str] = None
    evidence_file: Optional[str] = "unknown"
    evidence_line: Optional[int] = None
    evidence_code: List[str] = Field(default_factory=list)
    missing_detail: Optional[str] = None
    manual_test_hint: Optional[str] = None

    @field_validator("evidence_code", mode="before")
    @classmethod
    def _coerce_evidence_code(cls, v: object) -> list:
        return v if v is not None else []


class RequirementsResult(BaseModel):
    """Validation result for requirements analysis.

    Fields overall_verdict, verdict_reason, and implementation_summary have defaults
    to handle cases where the LLM response is truncated or malformed.
    The domain rules will still normalize the verdict if needed.
    """

    work_items_analysed: List[WorkItemAnalysed]
    requirements: List[Requirement]
    overall_verdict: str = "UNVERIFIABLE"
    verdict_reason: str = "AI response incomplete or truncated"
    implementation_summary: str = "See requirements array for status details"
