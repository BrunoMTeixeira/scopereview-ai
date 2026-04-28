import pytest
from src.services.static_analyzer import StaticAnalyzer

def test_unused_imports():
    code = "import os\nimport sys\nprint(sys.path)"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    # os no usado, sys usado no print
    assert any("Unused import: os" in f["title"] for f in findings)
    assert not any("Unused import: sys" in f["title"] for f in findings)

def test_unused_from_imports():
    code = "from collections import defaultdict, deque\nprint(defaultdict(list))"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    # deque no usado
    assert any("Unused import: deque" in f["title"] for f in findings)

def test_print_detection():
    code = "def hello():\n    print('world')"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    assert any("print() used instead of logging" in f["title"] for f in findings)

def test_broad_except_detection():
    code = "try:\n    do_something()\nexcept:\n    pass\nexcept Exception:\n    pass"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    assert sum(1 for f in findings if "Broad exception handler" in f["title"]) == 2

def test_debug_logging_level():
    code = "logging.basicConfig(level=logging.DEBUG)\nlogger.setLevel(level=DEBUG)"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    assert sum(1 for f in findings if "DEBUG logging level in production" in f["title"]) == 2

def test_missing_stdlib_imports():
    code = "d = timedelta(days=1)\np = Path('/tmp')"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    # Sem imports, deve detetar ambos
    assert any("`timedelta` used but never imported" in f["title"] for f in findings)
    assert any("`Path` used but never imported" in f["title"] for f in findings)

def test_unreachable_code():
    code = "def f():\n    pass\n    return 1"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    assert any("Unreachable code after `pass`" in f["title"] for f in findings)

def test_missing_rowcount():
    code = "cursor.execute('UPDATE users SET name = ?', (name,))\nconn.commit()"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    assert any("Missing rowcount check after UPDATE/DELETE" in f["title"] for f in findings)

def test_pii_in_logs():
    code = "log.info(f'User {email} logged in')\nlog.error(f'Failed password: {password}')"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    assert sum(1 for f in findings if "Possible PII exposed in logs" in f["title"]) == 2

def test_unvalidated_limit():
    code = "query = 'SELECT * FROM items LIMIT ?'\ncursor.execute(query, (limit,))"
    findings = StaticAnalyzer.analyze_file("test.py", code)
    assert any("Unvalidated LIMIT parameter in SQL query" in f["title"] for f in findings)
