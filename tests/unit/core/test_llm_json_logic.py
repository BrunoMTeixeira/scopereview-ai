import pytest
from src.core.llm_json import sanitize_llm_json_fragment

def test_sanitize_json_fixes_backslashes():
    """Testa se corrige barras invertidas mal escapadas."""
    # O regex r'\\(?!["\\/bfnrtu])' deve encontrar \ que não é seguido de caracteres válidos
    raw = '{"path": "C:\\Users\\test"}'
    sanitized = sanitize_llm_json_fragment(raw)
    # Deve duplicar a barra se ela não for um escape válido
    # Em JSON "C:\U" é inválido, deve ser "C:\\U"
    assert '\\\\Users' in sanitized

def test_sanitize_json_removes_trailing_commas():
    """Testa se remove vírgulas antes de fechar objeto ou array."""
    raw = '{"a": 1,}'
    sanitized = sanitize_llm_json_fragment(raw)
    assert sanitized == '{"a": 1}'
    
    raw_list = '{"a": [1, 2,]}'
    sanitized_list = sanitize_llm_json_fragment(raw_list)
    assert sanitized_list == '{"a": [1, 2]}'

def test_sanitize_json_noop_on_clean():
    """Testa se não estraga JSON já correto."""
    raw = '{"a": 1, "b": [2, 3]}'
    assert sanitize_llm_json_fragment(raw) == raw
