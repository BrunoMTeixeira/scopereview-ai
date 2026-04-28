# Requirements Traceability Matrix (RTM)

**Document Version:** 1.0
**Last Updated:** 2025-04-28
**Project:** ScopeReview AI - Automated PR Analysis for Azure DevOps

---

## Purpose

This Requirements Traceability Matrix demonstrates bidirectional traceability between:
- **Functional Requirements (FR)** — What the system must do
- **Non-Functional Requirements (NFR)** — How the system must perform
- **Implementation** — Where requirements are realized in code
- **Verification** — How requirements are tested and validated

This document serves as evidence of engineering rigor for academic evaluation.

---

## Functional Requirements (FR)

| ID | Requirement | Business Justification | Implementation | Test Coverage | Status |
|----|-------------|------------------------|----------------|---------------|--------|
| **FR-01** | **Automated Code Review**: The system SHALL analyze Python code in Azure DevOps PRs for security vulnerabilities (hardcoded secrets, broad exceptions, missing imports). | Reduces manual review effort and catches common security issues before merge. | `src/services/code_review_service.py:40-180`<br>`src/domain/static_checks.py` | `tests/unit/domain/test_static_checks.py`<br>`tests/system/test_review_endpoint.py` | ✅ Implemented |
| **FR-02** | **Requirements Validation**: The system SHALL cross-reference code changes with linked Azure DevOps Work Items to verify if acceptance criteria are met. | Ensures traceability between business requirements and implementation. | `src/services/requirements_review_service.py:25-120`<br>`src/infra/azure_devops.py:196-242` | `tests/unit/services/test_requirements_service.py`<br>`tests/integration/test_requirements_flow.py` | ✅ Implemented |
| **FR-03** | **Webhook Processing**: The system SHALL receive and authenticate Azure DevOps webhooks using HMAC-SHA256 signature validation. | Prevents unauthorized webhook injection and ensures request authenticity. | `src/api/webhook.py:45-85`<br>`src/core/rate_limit.py` | `tests/e2e/test_fastapi_endpoints.py:test_webhook_signature`<br>`tests/unit/api/test_webhook_security.py` | ✅ Implemented |
| **FR-04** | **PR Comment Publishing**: The system SHALL post analysis results as formatted Markdown comments on the originating Pull Request. | Provides developers with actionable feedback directly in their workflow. | `src/infra/azure_devops.py:261-277`<br>`src/templates/format_code_review.py` | `tests/integration/test_ado_comment_posting.py`<br>Manual verification via ngrok | ✅ Implemented |
| **FR-05** | **File Change Detection**: The system SHALL identify and analyze only modified files in a PR (ignoring `.md`, `.json`, lock files, images). | Focuses AI resources on code-only changes, reducing token costs. | `src/infra/azure_devops.py:135-194`<br>Constants in `azure_devops.py:10-31` | `tests/unit/infra/test_azure_devops_detailed.py:test_ignored_extensions` | ✅ Implemented |

---

## Non-Functional Requirements (NFR)

| ID | Requirement | Business Justification | Implementation | Test Coverage | Status |
|----|-------------|------------------------|----------------|---------------|--------|
| **NFR-01** | **Resilience**: The system SHALL retry transient HTTP failures (429, 5xx, network errors) with exponential backoff (max 3 attempts). | Handles temporary Azure API rate limits without failing entire PR analysis. | `src/core/resilience.py`<br>`@with_retry_on_transient_http_errors` decorator in `azure_ai.py` and `azure_devops.py` | `tests/unit/infra/test_azure_ai_adapter.py:test_retry_on_429`<br>`tests/unit/infra/test_azure_devops_adapter.py:test_network_resilience` | ✅ Implemented |
| **NFR-02** | **DoS Protection**: The system SHALL enforce rate limiting (max 10 webhooks/min) using a token bucket algorithm. | Prevents webhook flooding attacks and controls Azure AI API costs. | `src/core/rate_limit.py`<br>`src/api/webhook.py:check_rate_limit()` | `tests/unit/api/test_rate_limiting.py`<br>`tests/system/test_webhook_flooding.py` | ✅ Implemented |
| **NFR-03** | **Security - Prompt Injection Defense**: The system SHALL sanitize all user-controlled inputs (PR descriptions, file content) before sending to LLM. | Prevents adversarial prompts from manipulating AI verdicts. | `src/services/code_review_service.py:sanitize_user_input()`<br>`src/core/llm_json.py` | `tests/unit/services/test_prompt_injection.py`<br>Documented in `Security.md` | ✅ Implemented |
| **NFR-04** | **Observability**: The system SHALL expose real-time metrics (token usage, latency, health) via `/metrics` and `/health` endpoints. | Enables monitoring of AI costs and system health in production. | `src/core/metrics.py`<br>`src/api/health.py` | `tests/e2e/test_fastapi_endpoints.py:test_metrics_endpoint`<br>`verify_all.py` | ✅ Implemented |
| **NFR-05** | **Configuration Validation**: The system SHALL validate all critical environment variables (Azure tokens, ADO PAT) at startup and FAIL FAST if missing in production mode. | Prevents runtime failures due to misconfiguration; enforces fail-fast principle. | `src/core/config.py` (Pydantic Settings with `@field_validator`)<br>`load_settings()` | `tests/unit/core/test_config_validation.py`<br>Manual verification: `docker compose up` without `.env` | ✅ Implemented |

---

## Cross-Reference: Requirements → Code → Tests

### FR-01: Automated Code Review
- **Code**: `src/domain/static_checks.py:detect_print_usage()`, `detect_broad_exceptions()`
- **Tests**: `tests/unit/domain/test_static_checks.py::test_detect_hardcoded_secrets`
- **Coverage**: 100% (all static check functions have dedicated unit tests)

### FR-02: Requirements Validation
- **Code**: `src/services/requirements_review_service.py:execute()`
- **Tests**: `tests/integration/test_requirements_flow.py::test_requirements_agent_with_work_items`
- **Coverage**: 94% (integration test with mocked ADO Work Items API)

### NFR-01: Resilience
- **Code**: `src/core/resilience.py:with_retry_on_transient_http_errors()`
- **Tests**: `tests/unit/infra/test_azure_ai_adapter.py::test_complete_retry_on_429`
- **Coverage**: 88% (simulated 429, 503, network timeout scenarios)

### NFR-05: Configuration Validation
- **Code**: `src/core/config.py:Settings.validate_https_endpoints()`
- **Tests**: `tests/unit/core/test_config_validation.py::test_missing_azure_key_fails_in_production`
- **Coverage**: 100% (all validation paths tested)

---

## Test Coverage Summary

| Layer | Coverage | Test Count |
|-------|----------|------------|
| **Domain (Business Logic)** | 100% | 28 tests |
| **Services (Orchestration)** | 95% | 35 tests |
| **Infrastructure (Adapters)** | 89% | 25 tests |
| **API (Webhooks, Health)** | 100% | 15 tests |
| **Core (Config, Logging, Metrics)** | 92% | 10 tests |
| **Overall** | **92%** | **113 tests** |

---

## Verification Methods

| Verification Type | Method | Evidence |
|-------------------|--------|----------|
| **Unit Testing** | Pytest with mocks | `pytest tests/unit --cov=src` |
| **Integration Testing** | End-to-end flows with test doubles | `pytest tests/integration` |
| **System Testing** | Black-box API tests | `pytest tests/system` |
| **Static Analysis** | Flake8 linting | `verify_all.py` (Gate 2) |
| **Manual Testing** | ngrok tunneling to live Azure DevOps | Developer validation checklist |
| **Security Testing** | Prompt injection test cases | `tests/unit/services/test_prompt_injection.py` |

---

## Change History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-04-28 | Bruno Teixeira | Initial RTM for academic submission |

---

## Notes

- All requirements have **bidirectional traceability** (requirement → code → test).
- **NFR-01 (Resilience)** was added post-code-review to address transient failure handling.
- **NFR-05 (Config Validation)** was enhanced with Pydantic Settings for fail-fast behavior.
- Coverage gaps (<95%) are justified in `Quality-and-Testing.md` (e.g., Redis fallback paths).

---

**Document Owner:** Bruno Teixeira (ISEP - Engenharia Informática)
**Review Status:** ✅ Approved for Academic Submission
