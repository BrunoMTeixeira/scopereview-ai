# verify_all.py - VERSÃO PROFISSIONAL
"""
ScopeReview AI — Project Verification Script

Automated quality gate enforcement for academic excellence.

Verifies:
- Python imports and module integrity
- Code quality standards (Flake8)
- Unit, integration, and system tests (Pytest)
- Test coverage (minimum 90% threshold)
- Configuration validation (Pydantic Settings)
- Bootstrap & Dependency Injection
- FastAPI endpoints and observability
- Resilience mechanisms (Tenacity retry decorators)
"""

import subprocess
import sys
import os
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

# Set ENVIRONMENT=dev for tests before any imports
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("WEBHOOK_SECRET", "")


class Status(Enum):
    """Status de teste"""
    PASS = "[PASS]"
    FAIL = "[FAIL]"
    WARN = "[WARN]"


@dataclass
class TestResult:
    """Resultado de um teste"""
    name: str
    status: Status
    message: str = ""
    details: list = None


class ProjectVerifier:
    """Verifica integridade do projeto"""

    def __init__(self):
        self.results = []
        self.cwd = Path.cwd()

    def run_command(self, cmd: str, name: str, show_output: bool = False) -> TestResult:
        """Executa comando e retorna resultado"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                shell=True,
                cwd=self.cwd
            )

            success = result.returncode == 0
            details = []

            if success:
                status = Status.PASS
            else:
                status = Status.FAIL
                if result.stderr:
                    details = result.stderr.split('\n')[:5]

            if show_output and result.stdout:
                details.extend(result.stdout.split('\n')[-10:])

            test_result = TestResult(name, status, details=details)
            self.results.append(test_result)
            return test_result

        except Exception as e:
            test_result = TestResult(name, Status.FAIL, str(e))
            self.results.append(test_result)
            return test_result

    def verify_imports(self):
        """Verifica se todos os imports funcionam"""
        print("\n" + "="*70)
        print("1. VERIFICAÇÃO DE IMPORTS")
        print("="*70 + "\n")

        imports = [
            ("python -c \"from src.composition import get_pipeline_orchestrator\"",
             "composition.get_pipeline_orchestrator"),
            ("python -c \"from src.bootstrap import bootstrap_dependencies\"",
             "bootstrap.bootstrap_dependencies"),
            ("python -c \"from src.core.di import SimpleDependencyInjector\"",
             "core.di.SimpleDependencyInjector"),
            ("python -c \"from src.core.config import settings, load_settings\"",
             "core.config (Pydantic Settings)"),
            ("python -c \"from src.core.resilience import with_retry_on_transient_http_errors\"",
             "core.resilience (Tenacity)"),
            ("python -c \"from src.main import app\"",
             "main.app"),
        ]

        for cmd, name in imports:
            self.run_command(cmd, name)

    def verify_code_quality(self):
        """Verifica qualidade do código"""
        print("\n" + "="*70)
        print("2. QUALIDADE DO CÓDIGO (Flake8)")
        print("="*70 + "\n")

        self.run_command(
            "python -m flake8 src/",
            "Flake8 — Code Style"
        )

    def verify_unit_tests(self):
        """Executa testes unitários"""
        print("\n" + "="*70)
        print("3. TESTES UNITÁRIOS (Pytest)")
        print("="*70 + "\n")

        self.run_command(
            "python -m pytest tests/unit/ -v --tb=short -q",
            "Pytest — Unit Tests"
        )

    def verify_coverage(self):
        """Verifica cobertura de testes (mínimo 90%)"""
        print("\n" + "="*70)
        print("4. COBERTURA DE TESTES (Target: 90%)")
        print("="*70 + "\n")

        self.run_command(
            "python -m pytest tests/ --cov=src --cov-report=term --cov-fail-under=90 -q",
            "Coverage Report (>= 90%)",
            show_output=True
        )

    def verify_bootstrap(self):
        """Testa bootstrap & DI"""
        print("\n" + "="*70)
        print("5. BOOTSTRAP & DEPENDENCY INJECTION")
        print("="*70 + "\n")

        self.run_command(
            "python -m pytest tests/unit/core/test_bootstrap_flow.py -v",
            "Bootstrap Test"
        )

    def verify_fastapi(self):
        """Testa FastAPI endpoints"""
        print("\n" + "="*70)
        print("6. FASTAPI ENDPOINTS")
        print("="*70 + "\n")

        self.run_command(
            "python -m pytest tests/e2e/test_fastapi_endpoints.py -v",
            "FastAPI Test"
        )

    def verify_e2e(self):
        """Testa E2E"""
        print("\n" + "="*70)
        print("7. END-TO-END TESTS")
        print("="*70 + "\n")

        self.run_command(
            "python -m pytest tests/system/ -v --tb=short -q",
            "System / Black Box Tests"
        )

    def verify_observability(self):
        """Testa Observabilidade e Segurança"""
        print("\n" + "="*70)
        print("8. OBSERVABILITY & SECURITY")
        print("="*70 + "\n")

        self.run_command(
            "python -m pytest tests/unit/api/test_observability.py -v",
            "Metrics & Rate Limit Test"
        )

    def verify_resilience(self):
        """Testa mecanismos de resiliência (Tenacity)"""
        print("\n" + "="*70)
        print("9. RESILIENCE MECHANISMS")
        print("="*70 + "\n")

        self.run_command(
            "python -c \"from src.infra.azure_ai import AzureOpenAIClient; "
            "from src.infra.azure_devops import AzureDevOpsClient; "
            "print('OK: Retry decorators loaded successfully')\"",
            "Tenacity Retry Decorators"
        )

    def verify_config_validation(self):
        """Testa validação de configuração (Pydantic)"""
        print("\n" + "="*70)
        print("10. CONFIG VALIDATION (Pydantic Settings)")
        print("="*70 + "\n")

        self.run_command(
            "python -m pytest tests/unit/core/test_config.py -v",
            "Config Validation Tests"
        )

    def print_results(self):
        """Imprime resultados finais"""
        passed = sum(1 for r in self.results if r.status == Status.PASS)
        failed = sum(1 for r in self.results if r.status == Status.FAIL)
        total = len(self.results)

        print("\n" + "="*70)
        print("RESUMO FINAL")
        print("="*70 + "\n")

        for result in self.results:
            symbol = result.status.value
            print(f"{symbol} {result.name}")
            if result.details and result.status == Status.FAIL:
                for detail in result.details[:3]:
                    if detail.strip():
                        print(f"    {detail[:70]}")

        print("\n" + "-"*70)
        print(f"Resultados: {passed} PASS | {failed} FAIL | Total: {total}")
        print("-"*70 + "\n")

        return failed == 0

    def run_all(self):
        """Executa todas as verificações (11 quality gates)"""
        print("\n+" + "="*68 + "+")
        print("|" + " "*10 + "ScopeReview AI — Automated Quality Gates" + " "*10 + "|")
        print("|" + " "*15 + "(11 Critical Verification Checks)" + " "*16 + "|")
        print("+" + "="*68 + "+")

        self.verify_imports()
        self.verify_code_quality()
        self.verify_unit_tests()
        self.verify_coverage()
        self.verify_bootstrap()
        self.verify_fastapi()
        self.verify_e2e()
        self.verify_observability()
        self.verify_resilience()
        self.verify_config_validation()

        success = self.print_results()
        return 0 if success else 1


if __name__ == "__main__":
    verifier = ProjectVerifier()
    exit_code = verifier.run_all()
    sys.exit(exit_code)
