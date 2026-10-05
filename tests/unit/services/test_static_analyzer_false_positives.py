import pytest
from src.services.static_analyzer import StaticAnalyzer


# BUG 1: SQL Injection regex incorrectly matched %s parameterized placeholders

def test_sql_injection_safe_percent_s_not_flagged():
    """%s placeholder in cursor.execute must NEVER be flagged as SQL injection."""
    code = (
        'cursor.execute("SELECT id FROM users WHERE id = %s", (user_id,))\n'
        'cursor.execute("UPDATE orders SET status = %s WHERE id = %s", (status, oid))\n'
    )
    findings = StaticAnalyzer.analyze_file("order_service.py", code)
    sql = [f for f in findings if "SQL Injection" in f["title"]]
    assert sql == [], f"False positive: safe parameterized query flagged: {sql}"


def test_sql_injection_fstring_flagged():
    code = 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n'
    findings = StaticAnalyzer.analyze_file("vuln.py", code)
    assert any("SQL Injection" in f["title"] for f in findings)


def test_sql_injection_format_flagged():
    code = 'cursor.execute("SELECT * FROM users WHERE name = \'{}\'".format(name))\n'
    findings = StaticAnalyzer.analyze_file("vuln.py", code)
    assert any("SQL Injection" in f["title"] for f in findings)


def test_sql_injection_multiline_safe_not_flagged():
    code = (
        'cursor.execute(\n'
        '    "SELECT id, ref FROM orders LIMIT %s OFFSET %s",\n'
        '    (page_size, offset)\n'
        ')\n'
    )
    findings = StaticAnalyzer.analyze_file("order_service.py", code)
    sql = [f for f in findings if "SQL Injection" in f["title"]]
    assert sql == [], f"False positive: safe multi-line query flagged: {sql}"


# BUG 2: eval/exec rule fired on comments like '# NO EVAL!'

def test_eval_in_comment_not_flagged():
    code = (
        '# Static discount mappings - NO EVAL!\n'
        'DISCOUNT_RULES = {"early_bird": 0.10}\n'
    )
    findings = StaticAnalyzer.analyze_file("service.py", code)
    dangerous = [f for f in findings if "Dangerous Function" in f["title"]]
    assert dangerous == [], f"False positive: eval in comment flagged: {dangerous}"


def test_eval_in_code_is_flagged():
    code = "result = eval(user_input)\n"
    findings = StaticAnalyzer.analyze_file("vuln.py", code)
    assert any("Dangerous Function" in f["title"] for f in findings)


def test_exec_in_comment_not_flagged():
    code = (
        '# avoid exec() in production\n'
        'def safe_run():\n'
        '    return "ok"\n'
    )
    findings = StaticAnalyzer.analyze_file("runner.py", code)
    dangerous = [f for f in findings if "Dangerous Function" in f["title"]]
    assert dangerous == [], f"False positive: exec in comment flagged: {dangerous}"


def test_verify_false_in_comment_not_flagged():
    code = (
        '# Do NOT use verify=False in production\n'
        'response = requests.get(url, verify=True)\n'
    )
    findings = StaticAnalyzer.analyze_file("client.py", code)
    ssl = [f for f in findings if "SSL" in f["title"]]
    assert ssl == [], f"False positive: verify=False in comment flagged: {ssl}"


def test_verify_false_in_code_flagged():
    code = 'response = requests.get(url, verify=False)\n'
    findings = StaticAnalyzer.analyze_file("vuln.py", code)
    assert any("SSL" in f["title"] for f in findings)


# BUG 3: NameError matched class names in docstrings/comments

def test_path_in_docstring_not_flagged():
    code = (
        'def export_orders():\n'
        '    """Export to file (AC-10: Anti-Path Traversal)."""\n'
        '    safe_path = os.path.join("/var/data", filename)\n'
    )
    findings = StaticAnalyzer.analyze_file("service.py", code)
    ne = [f for f in findings if "NameError" in f["title"] and "Path" in f["title"]]
    assert ne == [], f"False positive: Path in docstring triggered NameError: {ne}"


def test_path_used_as_code_is_flagged():
    code = (
        'import os\n'
        'def get_path():\n'
        '    return Path("/tmp/exports")\n'
    )
    findings = StaticAnalyzer.analyze_file("service.py", code)
    assert any("NameError" in f["title"] and "Path" in f["title"] for f in findings)


def test_path_in_comment_not_flagged():
    code = (
        'from werkzeug.utils import secure_filename  # replaces pathlib.Path\n'
        'safe_name = secure_filename(filename)\n'
    )
    findings = StaticAnalyzer.analyze_file("service.py", code)
    ne = [f for f in findings if "NameError" in f["title"] and "Path" in f["title"]]
    assert ne == [], f"False positive: Path in comment triggered NameError: {ne}"


def test_eval_in_docstring_not_flagged():
    code = (
        'def apply_discount(order_id: int):\n'
        '    """Apply discount based on static rules (AC-03: No eval())."""\n'
        '    return 0.1\n'
    )
    findings = StaticAnalyzer.analyze_file('order_service.py', code)
    dangerous = [f for f in findings if 'Dangerous Function' in f['title']]
    assert dangerous == [], f'False positive: eval in docstring flagged: {dangerous}'


def test_eval_in_string_literal_not_flagged():
    code = 'abort(400, "eval() function is not supported")\n'
    findings = StaticAnalyzer.analyze_file('service.py', code)
    dangerous = [f for f in findings if 'Dangerous Function' in f['title']]
    assert dangerous == [], f'False positive: eval in string literal flagged: {dangerous}'


def test_numbered_diff_line_numbers():
    diff = (
        ' 100 | +    import sys\n'
        ' 298 | +    """Apply discount based on static rules (AC-03: No eval())."""\n'
        ' 350 | +    cursor.execute("UPDATE orders SET total = %s WHERE id = %s", (t, i))\n'
        ' 351 | +    db.commit()\n'
    )
    findings = StaticAnalyzer.analyze_file('order_service.py', diff)
    rowcount = [f for f in findings if 'rowcount' in f['title'].lower()]
    assert len(rowcount) == 1
    assert rowcount[0]['line'] == 350, f'Expected line 350, got {rowcount[0]["line"]}'
