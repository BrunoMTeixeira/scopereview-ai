"""Semantic Triage Gate — Pre-filter to classify PR risk and skip trivial changes.

This module implements a lightweight, deterministic classifier that runs
BEFORE any LLM invocation to reduce unnecessary token consumption.

Strategy:
    SKIP   — Trivial changes (docs, configs, lock files) → No LLM at all
    LIGHT  — Low-risk changes → Static Analyzer only (no LLM)
    FULL   — High-risk changes → Full pipeline (Static + LLM)

Reference: CodeRabbit/Qodo-style triage architecture (2024).
"""

import re
from enum import Enum
from typing import Dict

from ..core.logger import get_logger

log = get_logger("Triage")


class TriageLevel(str, Enum):
    SKIP = "skip"
    LIGHT = "light"
    FULL = "full"


# Files that never need LLM review
_SKIP_EXTENSIONS = frozenset({
    ".md", ".txt", ".rst", ".csv", ".json", ".lock",
    ".gitignore", ".dockerignore", ".editorconfig",
    ".env.example", ".prettierrc", ".eslintignore",
})

_SKIP_FILENAMES = frozenset({
    "package-lock.json", "yarn.lock", "poetry.lock",
    "requirements.txt", "Pipfile.lock",
    "LICENSE", "CHANGELOG.md", "README.md",
})

# Patterns that indicate security/business-critical code
_HIGH_RISK_PATTERNS = [
    re.compile(r"\b(password|secret|token|api_key|auth|credential)\b", re.I),
    re.compile(r"\b(cursor\.execute|\.query\(|\.raw\(|sql)\b", re.I),
    re.compile(r"\b(eval|exec|subprocess|os\.system|os\.popen)\b"),
    re.compile(r"\b(login|authenticate|authorize|session|jwt|oauth)\b", re.I),
    re.compile(r"\b(encrypt|decrypt|hash|hmac|bcrypt|sha256)\b", re.I),
    re.compile(r"\b(credit_card|ssn|nif|iban|pii)\b", re.I),
]


def classify_file(path: str, content: str) -> TriageLevel:
    """Classify a single file into a triage level.

    Args:
        path: File path (e.g., 'src/auth/login.py')
        content: File content or diff content

    Returns:
        TriageLevel indicating the review depth needed.
    """
    lower_path = path.lower()

    # Check extension-based skip
    for ext in _SKIP_EXTENSIONS:
        if lower_path.endswith(ext):
            return TriageLevel.SKIP

    # Check filename-based skip
    filename = lower_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if filename in _SKIP_FILENAMES:
        return TriageLevel.SKIP

    # Check for high-risk patterns in content
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(content):
            return TriageLevel.FULL

    # Default: still send to LLM, but could be LIGHT in future
    return TriageLevel.FULL


def triage_files(mapa: Dict[str, str]) -> Dict[TriageLevel, Dict[str, str]]:
    """Classify all files in a PR into triage buckets.

    Args:
        mapa: Dictionary mapping file paths to content/diffs.

    Returns:
        Dictionary with TriageLevel keys and file maps as values.
    """
    buckets: Dict[TriageLevel, Dict[str, str]] = {
        TriageLevel.SKIP: {},
        TriageLevel.LIGHT: {},
        TriageLevel.FULL: {},
    }

    for path, content in mapa.items():
        level = classify_file(path, content)
        buckets[level][path] = content
        if level == TriageLevel.SKIP:
            log.info("  [TRIAGE] SKIP '%s' (trivial file)", path)
        elif level == TriageLevel.LIGHT:
            log.info("  [TRIAGE] LIGHT '%s' (static only)", path)

    total = len(mapa)
    skipped = len(buckets[TriageLevel.SKIP])
    light = len(buckets[TriageLevel.LIGHT])
    full = len(buckets[TriageLevel.FULL])
    log.info(
        "Triage result: %d files → SKIP=%d, LIGHT=%d, FULL=%d",
        total, skipped, light, full,
    )

    return buckets
