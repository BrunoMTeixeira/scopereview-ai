import json

from src.core.llm_json import parse_llm_json_object, sanitize_llm_json_fragment


def test_sanitize_trailing_comma_roundtrip():
    assert json.loads(sanitize_llm_json_fragment('{"a": 1,}')) == {"a": 1}


def test_parse_strict_ok():
    raw = (
        '{"requirements": [], "work_items_analysed": [], '
        '"overall_verdict": "NO_REQUIREMENTS", "verdict_reason": "", "implementation_summary": ""}'
    )
    d = parse_llm_json_object(raw, log_context="test")
    assert d["overall_verdict"] == "NO_REQUIREMENTS"


def test_parse_json_repair_trailing_comma():
    broken = '{"x": 1, "y": 2,}'
    d = parse_llm_json_object(broken, log_context="test")
    assert d == {"x": 1, "y": 2}

import pytest
from unittest.mock import patch

def test_parse_json_root_not_dict_fallback():
    """Testa se a lista em json.loads força fallback para json_repair que depois também falha se for lista."""
    with pytest.raises(ValueError, match="json_repair returned non-object root"):
        parse_llm_json_object("[1, 2, 3]", log_context="test")

def test_parse_json_repair_returns_list():
    """Testa se json_repair também é validado para garantir que devolve dicionário."""
    # Como json.loads("[1, 2]") vai funcionar mas rejeitar, o fallback vai para json_repair("[1, 2]") que também vai devolver lista
    with pytest.raises(ValueError, match="json_repair returned non-object root"):
        parse_llm_json_object("[1, 2]", log_context="test")

def test_parse_json_repair_crash():
    """Testa se um crash completo do json_repair (Exception genérica) é devidamente registado e reraised."""
    with patch("src.core.llm_json.json_repair.loads", side_effect=Exception("Critical Failure")):
        with pytest.raises(Exception, match="Critical Failure"):
            parse_llm_json_object("{invalid}", log_context="test")
