"""Tests for the AST Skeleton Extractor (src.core.ast_skeleton)."""

from src.core.ast_skeleton import (
    skeletonize_python, skeletonize_file, skeletonize_map,
    _skeletonize_regex, compress_code, compress_map,
    extract_ac_terms,
)


SAMPLE_PYTHON = '''
import os
import sqlite3
from datetime import datetime

DEFAULT_IP = "127.0.0.1"

class UserManager:
    """Manages user operations and GDPR compliance."""

    def __init__(self, config):
        self.config = config
        self._initialize_db()

    def _initialize_db(self):
        """Creates database tables."""
        with sqlite3.connect(self.config.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL
                )
            """)
            conn.commit()

    def login_user(self, email: str, password: str, ip_address: str = DEFAULT_IP) -> str:
        """Authenticates a user and logs the access."""
        with sqlite3.connect(self.config.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, status FROM users WHERE email=?", (email,))
            row = cursor.fetchone()
            if row:
                return "token_abc"
            else:
                return "401 Unauthorized"

    def export_user_data(self, user_id: int) -> dict:
        """Exports user data for GDPR compliance."""
        print(f"DEBUG: Exporting data for user {user_id}")
        try:
            with sqlite3.connect(self.config.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE id=?", (user_id,))
                row = cursor.fetchone()
                return {"user": row}
        except Exception:
            pass
            return {"error": "Failed"}

    def bulk_suspend_users(self, user_ids_list) -> dict:
        """Suspends multiple users in one operation."""
        count = 0
        for uid in user_ids_list:
            with sqlite3.connect(self.config.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET status='Suspended' WHERE id=?", (uid,))
                count += 1
        return {"status": "success", "suspended_count": count}
'''


class TestSkeletonizePython:
    """Tests for Python AST skeleton extraction."""

    def test_preserves_imports(self):
        skeleton = skeletonize_python(SAMPLE_PYTHON)
        assert "import os" in skeleton
        assert "import sqlite3" in skeleton
        assert "from datetime import datetime" in skeleton

    def test_preserves_class_definition(self):
        skeleton = skeletonize_python(SAMPLE_PYTHON)
        assert "class UserManager" in skeleton

    def test_preserves_function_signatures(self):
        skeleton = skeletonize_python(SAMPLE_PYTHON)
        assert "def login_user" in skeleton
        assert "def export_user_data" in skeleton
        assert "def bulk_suspend_users" in skeleton

    def test_preserves_type_hints(self):
        skeleton = skeletonize_python(SAMPLE_PYTHON)
        assert "email: str" in skeleton or "email" in skeleton
        assert "-> str" in skeleton or "-> dict" in skeleton

    def test_removes_function_bodies(self):
        skeleton = skeletonize_python(SAMPLE_PYTHON)
        # Implementation details should be removed
        assert "cursor.execute" not in skeleton
        assert "conn.commit" not in skeleton
        assert "row = cursor.fetchone" not in skeleton
        assert "401 Unauthorized" not in skeleton

    def test_removes_print_statements(self):
        skeleton = skeletonize_python(SAMPLE_PYTHON)
        assert "print(" not in skeleton

    def test_preserves_docstrings(self):
        skeleton = skeletonize_python(SAMPLE_PYTHON)
        assert "Manages user operations" in skeleton or "Authenticates" in skeleton

    def test_significant_reduction(self):
        """Skeleton should be significantly shorter than original."""
        skeleton = skeletonize_python(SAMPLE_PYTHON)
        original_lines = len(SAMPLE_PYTHON.strip().splitlines())
        skeleton_lines = len(skeleton.strip().splitlines())
        # Should reduce by at least 50%
        assert skeleton_lines < original_lines * 0.5, (
            f"Skeleton ({skeleton_lines} lines) should be <50% of original ({original_lines} lines)"
        )

    def test_preserves_constants(self):
        skeleton = skeletonize_python(SAMPLE_PYTHON)
        assert "DEFAULT_IP" in skeleton

    def test_handles_syntax_error_gracefully(self):
        """Should fall back to regex for invalid Python."""
        bad_code = "def foo(\n  broken syntax"
        result = skeletonize_python(bad_code)
        assert "def foo" in result


class TestSkeletonizeRegex:
    """Tests for regex-based fallback."""

    def test_extracts_definitions(self):
        code = "class MyClass:\n    x = 1\ndef my_func():\n    return 42\nimport os\n"
        skeleton = _skeletonize_regex(code)
        assert "class MyClass" in skeleton
        assert "def my_func" in skeleton
        assert "import os" in skeleton
        assert "return 42" not in skeleton


class TestSkeletonizeFile:
    """Tests for file-type routing."""

    def test_python_file(self):
        skeleton = skeletonize_file("app.py", SAMPLE_PYTHON)
        assert "class UserManager" in skeleton
        assert "cursor.execute" not in skeleton

    def test_unknown_extension_uses_regex(self):
        code = "function login() {\n  return true;\n}\n"
        skeleton = skeletonize_file("app.js", code)
        assert "function login" in skeleton


class TestSkeletonizeMap:
    """Tests for batch skeletonization."""

    def test_processes_all_files(self):
        mapa = {
            "user_manager.py": SAMPLE_PYTHON,
            "README.md": "# Project docs\nLong description...",
        }
        result = skeletonize_map(mapa)
        assert "user_manager.py" in result
        assert "README.md" in result
        assert "class UserManager" in result["user_manager.py"]


COMPRESS_SAMPLE = '''import sqlite3
import logging

# Setup logging
log = logging.getLogger("test")

class Manager:
    def login(self, email: str) -> str:
        """Authenticates user."""
        log.info(f"Login attempt for {email}")
        # Check database
        with sqlite3.connect("db") as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, status FROM users WHERE email=?", (email,))
            row = cursor.fetchone()
            if row:
                user_id = row[0]
                status = row[1]
                if status == "GDPR_DELETED":
                    self.log_action(user_id, "LOGIN_FAILED")
                    return "403"
                self.log_action(user_id, "LOGIN_SUCCESS")
                return "token_abc"
            else:
                return "401 Unauthorized"

    def anonymize(self, user_id: int) -> dict:
        """GDPR anonymization."""
        log.info(f"Anonymizing {user_id}")
        print(f"DEBUG: anonymize {user_id}")
        with sqlite3.connect("db") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET first_name=\\'ANONYMIZED\\', status=\\'GDPR_DELETED\\' WHERE id=?",
                (user_id,)
            )
            conn.commit()
            self.log_action(user_id, "ANONYMIZE")
            return {"status": "success"}
'''


class TestCompressCode:
    """Tests for the muscle view compressor."""

    # AC terms that would be extracted from the GDPR acceptance criteria
    AC_TERMS = {
        "GDPR_DELETED", "ANONYMIZED", "LOGIN_FAILED", "LOGIN_SUCCESS",
        "000000000", "audit_logs", "gdpr_request_id",
        "success", "error", "status",
    }

    def test_preserves_sql(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert "SELECT id, status FROM users" in result
        assert "UPDATE users SET" in result

    def test_preserves_returns(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert 'return "403"' in result
        assert 'return "token_abc"' in result
        assert 'return "401 Unauthorized"' in result

    def test_preserves_key_literals(self):
        result = compress_code(COMPRESS_SAMPLE, ac_terms=self.AC_TERMS)
        assert "GDPR_DELETED" in result
        assert "LOGIN_FAILED" in result
        assert "LOGIN_SUCCESS" in result
        assert "ANONYMIZED" in result

    def test_preserves_conditionals(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert "if row:" in result or "if status ==" in result

    def test_preserves_method_calls(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert "self.log_action" in result

    def test_removes_logging(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert "log.info" not in result

    def test_removes_print(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert "print(" not in result

    def test_removes_comments(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert "# Setup logging" not in result
        assert "# Check database" not in result

    def test_adds_omitted_markers(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert "# ..." in result

    def test_preserves_definitions(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert "class Manager:" in result
        assert "def login" in result
        assert "def anonymize" in result

    def test_preserves_docstrings(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert "Authenticates" in result or '"""' in result

    def test_shorter_than_original(self):
        result = compress_code(COMPRESS_SAMPLE)
        assert len(result.splitlines()) < len(COMPRESS_SAMPLE.strip().splitlines())


class TestCompressMap:
    """Tests for batch compression."""

    def test_compresses_all_files(self):
        mapa = {
            "app.py": COMPRESS_SAMPLE,
            "utils.py": "import os\n# comment\ndef helper():\n    return 42\n",
        }
        result = compress_map(mapa, ac_terms={"GDPR_DELETED"})
        assert "app.py" in result
        assert "utils.py" in result
        assert "log.info" not in result["app.py"]


class TestExtractACTerms:
    """Tests for dynamic AC term extraction."""

    SAMPLE_WORK_ITEMS = [
        {
            "id": 12,
            "title": "User Manager (RGPD & Audit)",
            "acceptance_criteria": (
                "A base de dados deve conter uma tabela `audit_logs` com as colunas: "
                "id, user_id, action, ip_address, timestamp, com foreign key para users(id).\n"
                "O status deve ser 'GDPR_DELETED' e password_hash invalidado.\n"
                "O email deve ser replaced com deleted_{id}@anonymized.local.\n"
                "O phone field must be '000000000'.\n"
                "Login falhados devem ser registados como LOGIN_FAILED.\n"
                "Login bem-sucedidos como LOGIN_SUCCESS.\n"
                "Cada export deve conter unique gdpr_request_id.\n"
                "first_name e last_name devem ser 'ANONYMIZED'.\n"
                "Bulk suspension deve criar BULK_SUSPEND audit log."
            ),
        }
    ]

    def test_extracts_upper_case_constants(self):
        terms = extract_ac_terms(self.SAMPLE_WORK_ITEMS)
        assert "GDPR_DELETED" in terms
        assert "LOGIN_FAILED" in terms
        assert "LOGIN_SUCCESS" in terms
        assert "BULK_SUSPEND" in terms
        assert "ANONYMIZED" in terms

    def test_extracts_quoted_strings(self):
        terms = extract_ac_terms(self.SAMPLE_WORK_ITEMS)
        assert "000000000" in terms

    def test_extracts_snake_case_identifiers(self):
        terms = extract_ac_terms(self.SAMPLE_WORK_ITEMS)
        assert "audit_logs" in terms
        assert "gdpr_request_id" in terms
        assert "ip_address" in terms
        assert "password_hash" in terms
        assert "first_name" in terms
        assert "last_name" in terms

    def test_excludes_stopwords(self):
        terms = extract_ac_terms(self.SAMPLE_WORK_ITEMS)
        # Common English words should be excluded
        assert "must" not in terms
        assert "should" not in terms

    def test_includes_base_literals(self):
        terms = extract_ac_terms([])
        assert "success" in terms
        assert "error" in terms
        assert "status" in terms

    def test_empty_work_items(self):
        terms = extract_ac_terms([])
        assert len(terms) >= 3  # At least base literals

    def test_works_with_compress_code(self):
        """End-to-end: AC extraction feeds into code compression."""
        terms = extract_ac_terms(self.SAMPLE_WORK_ITEMS)
        result = compress_code(COMPRESS_SAMPLE, ac_terms=terms)
        # Should preserve lines matching AC terms
        assert "GDPR_DELETED" in result
        assert "ANONYMIZED" in result
        assert "LOGIN_FAILED" in result
        # Should still remove logging
        assert "log.info" not in result

