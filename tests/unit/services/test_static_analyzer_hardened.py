import pytest
from src.services.static_analyzer import StaticAnalyzer

def test_static_analyzer_obfuscated_one_liner():
    """
    TESTE DE RIGOR: Cdigo condensado numa linha.
    O sistema deve detetar os problemas mesmo sem quebras de linha bonitas.
    """
    # Exemplo: import no usado e print escondido
    code = "import os; import sys; print('hack'); sys.exit(0)"
    findings = StaticAnalyzer.analyze_file("evil.py", code)
    
    # Deve detetar o print e o import 'os' no usado
    assert any("print() used instead of logging" in f["title"] for f in findings)
    assert any("Unused import: os" in f["title"] for f in findings)

def test_static_analyzer_utf8_resilience():
    """
    TESTE DE RESILINCIA: Comentários com emojis e strings complexas.
    O sistema no deve crashar nem perder a contagem de linhas.
    """
    code = """# 🚀 Critical logic below
def processar():
    dados = "💡 Validao"
    print(dados) # ⚠️ Deprecated
"""
    findings = StaticAnalyzer.analyze_file("intl.py", code)
    assert any("print() used instead of logging" in f["title"] for f in findings)
    # A linha do print deve ser a 4
    finding = next(f for f in findings if "print" in f["title"])
    assert finding["line"] == 4

def test_static_analyzer_malformed_input():
    """Testa se o sistema sobrevive a ficheiros vazios ou binários acidentais."""
    assert StaticAnalyzer.analyze_file("empty.py", "") == []
    # Simular bytes nulos (common in binary files)
    assert StaticAnalyzer.analyze_file("binary.py", "\x00\x01\x02\x03") == []
