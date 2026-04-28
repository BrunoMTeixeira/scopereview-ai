import pytest
from src.services.static_analyzer import StaticAnalyzer

def test_analyzer_detects_unused_imports():
    """Testa se o analisador deteta imports não utilizados."""
    analyzer = StaticAnalyzer()
    code = 'import os\nimport sys\nprint(os.name)'
    
    findings = analyzer.analyze_file("test.py", code)
    
    unused_sys = [f for f in findings if "Unused import: sys" in f["title"]]
    assert len(unused_sys) > 0
    # os não deve estar nos findings porque é usado
    unused_os = [f for f in findings if "Unused import: os" in f["title"]]
    assert len(unused_os) == 0

def test_analyzer_detects_print_usage():
    """Testa se o analisador deteta o uso de print()."""
    analyzer = StaticAnalyzer()
    code = 'def test():\n    print("debug message")'
    
    findings = analyzer.analyze_file("test.py", code)
    
    print_findings = [f for f in findings if "print() used instead of logging" in f["title"]]
    assert len(print_findings) > 0

def test_analyzer_detects_broad_except():
    """Testa se o analisador deteta except: ou except Exception:."""
    analyzer = StaticAnalyzer()
    code = 'try:\n    do_something()\nexcept:\n    pass'
    
    findings = analyzer.analyze_file("test.py", code)
    
    except_findings = [f for f in findings if "Broad exception handler" in f["title"]]
    assert len(except_findings) > 0

def test_analyzer_detects_missing_imports():
    """Testa se o analisador deteta o uso de tipos do stdlib sem import."""
    analyzer = StaticAnalyzer()
    code = 'd = defaultdict(int)\nd["key"] = 1'
    
    findings = analyzer.analyze_file("test.py", code)
    
    missing_import = [f for f in findings if "NameError" in f["title"] and "defaultdict" in f["title"]]
    assert len(missing_import) > 0

def test_analyzer_clean_code():
    """Testa código que cumpre todas as regras."""
    analyzer = StaticAnalyzer()
    code = 'import logging\n\nlog = logging.getLogger(__name__)\nlog.info("Starting")'
    
    findings = analyzer.analyze_file("test.py", code)
    assert len(findings) == 0
