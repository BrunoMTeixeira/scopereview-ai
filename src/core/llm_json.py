"""
Shared JSON handling for LLM outputs: light sanitization + json-repair fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

import json_repair

from .logger import get_logger

log = get_logger("LLMJson")


def sanitize_llm_json_fragment(raw: str) -> str:
    """Fix common invalid escapes, trailing commas, and unescaped control chars."""
    # 1. Fix single backslashes that are not part of a valid escape sequence
    fixed = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", raw)
    # 2. Fix trailing commas in objects/arrays
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    # 3. Strip ASCII control characters (0-31) except for valid ones like \n, \r, \t
    fixed = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", fixed)
    return fixed


def parse_llm_json_object(raw: str, *, log_context: str = "llm") -> Dict[str, Any]:
    """
    Parse a JSON object from an LLM fragment. Tries strict json.loads first,
    then json_repair.loads (handles unescaped quotes, newlines in strings, etc.).
    """
    text = sanitize_llm_json_fragment(raw.strip())
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("root JSON value must be an object")
        return data
    except (json.JSONDecodeError, ValueError) as err:
        log.warning("%s: json.loads failed (%s); retrying with json_repair", log_context, err)
        try:
            data = json_repair.loads(text)
            if not isinstance(data, dict):
                raise ValueError("json_repair returned non-object root")
            return data
        except Exception as err2:  # noqa: BLE001 — surface any repair failure
            log.error("%s: json_repair.loads failed: %s", log_context, err2)
            raise
