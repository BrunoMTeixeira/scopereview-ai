import pytest
from src.domain.code_verdict import CodeReviewVerdict

class TestCodeReviewVerdict:
    def test_evaluate_clean_score(self):
        engine = CodeReviewVerdict(max_high_block=3)
        raw = []
        unique, score, approved = engine.evaluate(raw)
        
        assert score == 10
        assert approved is True
        assert unique == []

    def test_evaluate_deduplication(self):
        engine = CodeReviewVerdict(max_high_block=3)
        raw = [
            {"file": "a.py", "line": 10, "type": "SQL", "severity": "high"},
            {"file": "a.py", "line": 10, "type": "SQL", "severity": "low"}  # Dupe location and type
        ]
        unique, score, approved = engine.evaluate(raw)
        
        assert len(unique) == 1
        assert unique[0]["severity"] == "high"

    def test_evaluate_penalties(self):
        engine = CodeReviewVerdict(max_high_block=3)
        raw = [
            {"file": "a.py", "line": 10, "type": "T1", "severity": "medium"},
            {"file": "a.py", "line": 20, "type": "T2", "severity": "low"}
        ]
        # penalty: 1.5 + 0.5 = 2.0 -> Score = 10 - 2 = 8
        unique, score, approved = engine.evaluate(raw)
        
        assert score == 8
        assert approved is True

    def test_evaluate_rejects_on_critical(self):
        engine = CodeReviewVerdict(max_high_block=3)
        raw = [
            {"file": "a.py", "line": 10, "type": "T1", "severity": "critical"}
        ]
        unique, score, approved = engine.evaluate(raw)
        
        assert approved is False
        assert score == 5  # 10 - 5.0

    def test_evaluate_rejects_on_max_high(self):
        engine = CodeReviewVerdict(max_high_block=2)
        raw = [
            {"file": "a.py", "line": 10, "type": "T1", "severity": "high"},
            {"file": "a.py", "line": 20, "type": "T2", "severity": "high"}
        ]
        unique, score, approved = engine.evaluate(raw)
        
        # 2 high == max_high_block (2). Should block.
        assert approved is False
        assert score == 4  # 10 - 6.0

    def test_evaluate_rejects_on_low_score(self):
        engine = CodeReviewVerdict(max_high_block=5)
        raw = [
            {"file": "a.py", "line": 1, "type": "T", "severity": "medium"},
            {"file": "a.py", "line": 2, "type": "T", "severity": "medium"},
            {"file": "a.py", "line": 3, "type": "T", "severity": "medium"}
        ]
        # penalty 4.5 -> score 6
        unique, score, approved = engine.evaluate(raw)
        
        assert approved is False
        assert score == 6
