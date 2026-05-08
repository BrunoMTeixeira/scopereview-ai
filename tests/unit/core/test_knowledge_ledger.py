"""Tests for the Cross-Agent Knowledge Ledger (src.core.knowledge_ledger)."""

from src.core.knowledge_ledger import build_ledger, format_ledger_for_prompt


class TestBuildLedger:
    """Unit tests for mapping findings to NFRs."""

    def test_unused_import_maps_to_nf01(self):
        findings = [{"title": "Unused import: json", "file": "app.py", "line": 3}]
        ledger = build_ledger(findings)
        assert "NF-01" in ledger
        assert ledger["NF-01"]["status"] == "PARTIAL"
        assert "app.py:3" in ledger["NF-01"]["evidence"]

    def test_print_maps_to_nf02(self):
        findings = [{"title": "print() used instead of logging", "file": "svc.py", "line": 42}]
        ledger = build_ledger(findings)
        assert "NF-02" in ledger

    def test_debug_level_maps_to_nf03(self):
        findings = [{"title": "DEBUG logging level in production", "file": "cfg.py", "line": 10}]
        ledger = build_ledger(findings)
        assert "NF-03" in ledger

    def test_broad_except_maps_to_nf05(self):
        findings = [{"title": "Broad exception handler", "file": "svc.py", "line": 99}]
        ledger = build_ledger(findings)
        assert "NF-05" in ledger

    def test_unreachable_code_maps_to_nf06(self):
        findings = [{"title": "Unreachable code after pass", "file": "svc.py", "line": 55}]
        ledger = build_ledger(findings)
        assert "NF-06" in ledger

    def test_hardcoded_secret_maps_to_sec01(self):
        findings = [{"title": "Hardcoded secret detected", "file": "cfg.py", "line": 7}]
        ledger = build_ledger(findings)
        assert "SEC-01" in ledger

    def test_sql_injection_maps_to_sec02(self):
        findings = [{"title": "SQL Injection risk in query", "file": "db.py", "line": 20}]
        ledger = build_ledger(findings)
        assert "SEC-02" in ledger

    def test_pii_in_logs_maps_to_sec03(self):
        findings = [{"title": "Possible PII exposed in logs", "file": "auth.py", "line": 15}]
        ledger = build_ledger(findings)
        assert "SEC-03" in ledger

    def test_multiple_findings_same_nfr(self):
        """Multiple unused imports should aggregate under a single NF-01."""
        findings = [
            {"title": "Unused import: os", "file": "a.py", "line": 1},
            {"title": "Unused import: sys", "file": "a.py", "line": 2},
        ]
        ledger = build_ledger(findings)
        assert "NF-01" in ledger
        assert len(ledger["NF-01"]["evidence"]) == 2

    def test_no_matching_findings(self):
        findings = [{"title": "Something unrelated", "file": "x.py", "line": 1}]
        ledger = build_ledger(findings)
        assert len(ledger) == 0

    def test_empty_findings(self):
        ledger = build_ledger([])
        assert len(ledger) == 0

    def test_mixed_findings(self):
        """Multiple different NFRs detected from different findings."""
        findings = [
            {"title": "Unused import: json", "file": "a.py", "line": 1},
            {"title": "print() used instead of logging", "file": "b.py", "line": 10},
            {"title": "Broad exception handler", "file": "c.py", "line": 20},
        ]
        ledger = build_ledger(findings)
        assert "NF-01" in ledger
        assert "NF-02" in ledger
        assert "NF-05" in ledger
        assert len(ledger) == 3


class TestFormatLedgerForPrompt:
    """Unit tests for ledger prompt formatting."""

    def test_empty_ledger_returns_none(self):
        assert format_ledger_for_prompt({}) is None

    def test_format_contains_header(self):
        ledger = {
            "NF-01": {
                "nfr_id": "NF-01",
                "label": "Sem Imports Não Utilizados",
                "status": "PARTIAL",
                "evidence": ["app.py:3"],
                "titles": ["Unused import: json"],
            }
        }
        result = format_ledger_for_prompt(ledger)
        assert "PRE-VERIFIED NFRs" in result
        assert "NF-01" in result
        assert "PARTIAL" in result
        assert "app.py:3" in result

    def test_format_limits_evidence(self):
        """Should show at most 3 evidence entries."""
        ledger = {
            "NF-01": {
                "nfr_id": "NF-01",
                "label": "Sem Imports",
                "status": "PARTIAL",
                "evidence": ["a.py:1", "b.py:2", "c.py:3", "d.py:4", "e.py:5"],
                "titles": ["Unused import"] * 5,
            }
        }
        result = format_ledger_for_prompt(ledger)
        # Should contain first 3 but not the 4th/5th
        assert "a.py:1" in result
        assert "c.py:3" in result
        assert "d.py:4" not in result
