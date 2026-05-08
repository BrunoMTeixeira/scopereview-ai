"""Tests for the AST Skeleton Extractor (src.core.ast_skeleton)."""

from src.core.ast_skeleton import skeletonize_python, skeletonize_file, skeletonize_map, _skeletonize_regex


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
