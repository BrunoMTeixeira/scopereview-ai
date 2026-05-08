"""Tests for the Semantic Triage Gate (src.core.triage)."""

from src.core.triage import classify_file, triage_files, TriageLevel


class TestClassifyFile:
    """Unit tests for individual file classification."""

    def test_skip_markdown(self):
        assert classify_file("README.md", "# Hello") == TriageLevel.SKIP

    def test_skip_gitignore(self):
        assert classify_file(".gitignore", "*.pyc") == TriageLevel.SKIP

    def test_skip_lock_file(self):
        assert classify_file("package-lock.json", "{}") == TriageLevel.SKIP

    def test_skip_requirements_txt(self):
        assert classify_file("requirements.txt", "flask==2.0") == TriageLevel.SKIP

    def test_skip_csv(self):
        assert classify_file("data/export.csv", "a,b,c") == TriageLevel.SKIP

    def test_skip_license_txt(self):
        assert classify_file("LICENSE.txt", "MIT") == TriageLevel.SKIP

    def test_full_python_with_password(self):
        code = "password = input('Enter password:')"
        assert classify_file("auth/login.py", code) == TriageLevel.FULL

    def test_full_sql_injection_risk(self):
        code = 'cursor.execute(f"SELECT * FROM users WHERE id = {uid}")'
        assert classify_file("db/queries.py", code) == TriageLevel.FULL

    def test_full_eval_usage(self):
        code = 'result = eval(user_input)'
        assert classify_file("utils/parser.py", code) == TriageLevel.FULL

    def test_full_jwt_handling(self):
        code = 'token = jwt.encode(payload, secret)'
        assert classify_file("auth/tokens.py", code) == TriageLevel.FULL

    def test_full_normal_python(self):
        """Normal code without high-risk patterns still gets FULL review."""
        code = "def add(a, b):\n    return a + b"
        assert classify_file("utils/math.py", code) == TriageLevel.FULL

    def test_full_secret_in_code(self):
        code = 'api_key = "sk-abc123def456"'
        assert classify_file("config.py", code) == TriageLevel.FULL

    def test_skip_env_example(self):
        assert classify_file(".env.example", "DB_HOST=localhost") == TriageLevel.SKIP

    def test_case_insensitive_path(self):
        assert classify_file("docs/CHANGELOG.MD", "## v1.0") == TriageLevel.SKIP


class TestTriageFiles:
    """Unit tests for batch file triage."""

    def test_mixed_files(self):
        mapa = {
            "README.md": "# Project",
            "src/auth.py": "password = hash(input)",
            ".gitignore": "*.pyc",
            "src/utils.py": "def helper(): pass",
        }
        buckets = triage_files(mapa)

        assert "README.md" in buckets[TriageLevel.SKIP]
        assert ".gitignore" in buckets[TriageLevel.SKIP]
        assert "src/auth.py" in buckets[TriageLevel.FULL]
        assert "src/utils.py" in buckets[TriageLevel.FULL]

    def test_all_trivial(self):
        mapa = {
            "README.md": "# Docs",
            "CHANGELOG.md": "## v1.0",
            "requirements.txt": "flask",
        }
        buckets = triage_files(mapa)

        assert len(buckets[TriageLevel.SKIP]) == 3
        assert len(buckets[TriageLevel.FULL]) == 0

    def test_empty_input(self):
        buckets = triage_files({})
        assert len(buckets[TriageLevel.SKIP]) == 0
        assert len(buckets[TriageLevel.FULL]) == 0
