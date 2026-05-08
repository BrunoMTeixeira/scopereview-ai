import pytest
from src.services.static_analyzer import StaticAnalyzer

# =====================================================================
# BUGGY VS BULLETPROOF REGRESSION SUITE
# =====================================================================
# This suite proves the deterministic gates (Static Analyzer) reliably
# catch known bad patterns, and properly ignore secure patterns,
# effectively avoiding False Positives and False Negatives.

BUGGY_CODE = """
from datetime import datetime
import json

def bad_function(user_id):
    api_key = "abcdef1234567890abcdef"
    
    print("Fetching user data...")
    
    try:
        cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
        
        cursor.execute("UPDATE users SET login = 1")
        db.commit()
        
        cursor.execute("SELECT * FROM logs LIMIT ?", (user_id,))
        
        logger.level = logging.DEBUG
        
        log.info(f"User email is {user.email} and password is {user.password}")
        
        expire = datetime.now() + timedelta(days=1)
        
        if not user:
            pass
            return None
            
    except Exception:
        pass
"""

BULLETPROOF_CODE = """
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def good_function(user_id: int):
    # Secret loaded from environment, not hardcoded
    api_key = os.environ.get("API_KEY")
    
    # Proper logging instead of print
    logger.info("Fetching user data...")
    
    try:
        # Parameterized query (No SQL Injection)
        query = "SELECT * FROM users WHERE id = ?"
        cursor.execute(query, (user_id,))
        
        # Checking rowcount after update
        cursor.execute("UPDATE users SET login = 1 WHERE id = ?", (user_id,))
        if cursor.rowcount == 0:
            logger.warning("No rows updated")
        db.commit()
        
        # Guard against negative limit
        limit = max(1, user_id)
        cursor.execute("SELECT * FROM logs LIMIT ?", (limit,))
        
        # PII is redacted or not logged
        logger.info("User accessed the system.")
        
        # Dependencies imported properly
        expire = datetime.now() + timedelta(days=1)
        
        if not user:
            return None
            
    except ValueError as e:
        # Specific exception catching
        logger.error("Value error: %s", e)
        raise
"""

def test_systemic_regression_buggy_code():
    """Verifica se todas as regras estáticas disparam no código Buggy (Zero False Negatives)."""
    analyzer = StaticAnalyzer()
    findings = analyzer.analyze_file("buggy.py", BUGGY_CODE)
    
    titles = [f["title"] for f in findings]
    
    # 1. Unused import
    assert any("Unused import: json" in t for t in titles), "Failed to detect unused import"
    # 2. print() used instead of logging
    assert any("print() used instead of logging" in t for t in titles), "Failed to detect print()"
    # 3. Broad exception handler
    assert any("Broad exception handler" in t for t in titles), "Failed to detect broad except"
    # 4. DEBUG logging level in production
    assert any("DEBUG logging level" in t for t in titles), "Failed to detect DEBUG level"
    # 5. Missing imports (NameError)
    assert any("NameError: `timedelta`" in t for t in titles), f"Failed to detect missing import. Titles: {titles}"
    # 6. Unreachable code
    assert any("Unreachable code" in t for t in titles), "Failed to detect unreachable code"
    # 7. Missing rowcount check
    assert any("rowcount" in t.lower() for t in titles), "Failed to detect missing rowcount"
    # 8. PII in logs
    assert any("PII" in t for t in titles), "Failed to detect PII in logs"
    # 9. Unvalidated LIMIT
    assert any("LIMIT" in t for t in titles), "Failed to detect unvalidated LIMIT"
    # 10. Hardcoded secret
    assert any("Hardcoded secret" in t for t in titles), "Failed to detect hardcoded secret"
    # 11. SQL Injection Risk
    assert any("SQL Injection" in t for t in titles), "Failed to detect SQL Injection"
    
    # Must have exactly 11 distinct issues
    assert len(findings) == 11

def test_systemic_regression_bulletproof_code():
    """Verifica se NENHUMA regra dispara no código Bulletproof (Zero False Positives)."""
    analyzer = StaticAnalyzer()
    findings = analyzer.analyze_file("bulletproof.py", BULLETPROOF_CODE)
    
    # If the code is perfect, the static analyzer should return exactly 0 findings.
    assert len(findings) == 0, f"Expected 0 findings for bulletproof code, but got {len(findings)}: {findings}"
