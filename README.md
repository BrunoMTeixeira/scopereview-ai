# ScopeReview AI — Automated PR Analysis for Azure DevOps

> **Advanced Academic Proof-of-Concept** demonstrating a hybrid pipeline system for automated **code review** and *
*requirements validation** in Azure DevOps Pull Requests, combining Deterministic Static Analysis with LLM agents via
Azure AI Foundry.

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)
[![Azure AI](https://img.shields.io/badge/Azure-AI_Foundry-0078D4?style=flat&logo=microsoftazure&logoColor=white)](https://ai.azure.com/)

**Full documentation** (C4, sequences, domain, quality): see the companion Azure DevOps Wiki repo *
*`poc-code-review.wiki`** — start at `Home.md` or `Quality-and-Testing.md`.

---

## What it does

When a developer creates or updates a Pull Request on Azure DevOps, the system automatically runs a sequential
orchestrator:

1. **Code Review Agent (Phase 1: Static Checks)** — Deterministically scans for security risks (print usage, broad
   exceptions, missing imports, etc.).
2. **Code Review Agent (Phase 2: LLM Analysis)** — AI analyzes the logical flow, security edge cases, and calculates a
   deterministic score.
3. **Requirements Validation Agent** — Cross-references code with ADO Work Items, verifying if business rules and
   acceptance criteria were actually implemented.

```
ADO Webhook → FastAPI Orchestrator → Pipeline
                                         ↓
                                    1. Static Analysis + Code Review Agent
                                         ↓
                                    2. Requirements Agent (Injects Context)
                                         ↓
                                    2x Markdown PR Reports
```

---

## Engineering Highlights

| Feature | Detail                                                                                                          |
|---------|-----------------------------------------------------------------------------------------------------------------|
| **Hexagonal Architecture** | Clean separation between domain logic and infrastructure (Azure AI, ADO, Redis) using Ports & Adapters pattern. |
| **High Test Coverage** | **90% code coverage** across all layers (Unit + Integration + System tests).                                    |
| **Security Controls** | Implements defenses against Prompt Injection, URL Manipulation, and PII leakage.                                |
| **Optimized HTTP** | Connection pooling via `requests.Session` reduces latency on ADO/Azure API calls.                               |
| **Resilient Design** | Exponential backoff with jitter for rate-limit handling (429) and automatic JSON repair for LLM responses.      |
| **DoS Protection** | Token-bucket rate limiting (10 req/min) prevents webhook flooding and controls AI costs.                        |
| **Observability** | Real-time metrics endpoint (`/metrics`) tracking token usage, latency, and system health.                       |
| **Quality Gates** | Automated `verify_all.py` script enforcing 11 quality checks before deployment.                                 |

---

## Quick Start

### 1️⃣ Clone & Setup

```bash
git clone https://github.com/your-org/poc-code-review.git
cd poc-code-review
cp .env.example .env
```

### 2️⃣ Run with Docker

```bash
docker compose up --build
```

Verify health: `curl http://localhost:8000/health`
Verify metrics: `curl http://localhost:8000/metrics`

### 3️⃣ Local Tunnel: ngrok (Fundamental for Dev)

Because Azure DevOps needs to reach your local machine to send webhooks, **ngrok is required** for the local development
feedback loop:

1. **Install & Run:**
   ```bash
   ngrok http 8000
   ```
2. **Configure ADO:** Copy the `https` Forwarding URL (e.g., `https://abcd-123.ngrok-free.app`) and use it as the base
   for your **Service Hook** Action URL:
   `https://<your-ngrok-id>.ngrok-free.app/webhook/orchestrate`

---

## Project Structure

```text
src/
├── core/               # DI Container, Config, Logger, JSON Repair
├── domain/             # Pure business rules (Verdict logic)
├── ports/              # Protocol interfaces (AI, ADO, Dedup)
├── infra/              # Adapters (Azure OpenAI, ADO REST, Redis)
├── services/           # Orchestrator, Code Review, Requirements Review
├── templates/          # Prompts and Markdown formatters
tests/
├── unit/               # Unit tests (Mocked Infra, Core, Services)
├── system/             # Black-box API tests
└── e2e/                # Lifecycle and Startup tests
verify_all.py           # The final quality gate (11 checks)
```

---

## 🛡️ Quality & Verification

The project implements rigorous quality standards through an automated verification pipeline designed for academic
excellence.

### Run Full Verification

```bash
python verify_all.py
```

This script executes **11 critical checks**:

- ✓ **Imports**: Path integrity.
- ✓ **Style**: Flake8 linting (Strict 120 chars policy).
- ✓ **Unit Tests**: Full `pytest` suite with **113+ scenarios**.
- ✓ **Coverage**: Minimum **90% threshold** achieved (**100% on API Webhooks**).
- ✓ **Security**: PII redaction and Prompt Isolation rules verified.
- ✓ **Bootstrap**: Dependency Injection wiring validation.
- ✓ **FastAPI**: Endpoint health (Deep Health Check) and lifespan lifecycle.
- ✓ **Observability**: Metrics collection and Rate Limiting logic validation.
- ✓ **System Flow**: End-to-end orchestration logic.

### Local Test Execution

```bash
# Run tests with terminal report
python -m pytest tests --cov=src --cov-report=term
```

---

## Architecture Patterns

- **Dependency Injection**: Services are decoupled from infrastructure via `SimpleDependencyInjector`.
- **Ports & Adapters**: Allows swapping Azure AI for local models without changing business logic.
- **Background Tasks**: PR analysis is non-blocking; FastAPI returns 200 OK immediately and processes in the background.

---

## Full Documentation

For detailed guides, see the [Wiki](../../wiki):

- **[🏗️ Architecture](../../wiki/Architecture.md)** — C4 Diagrams & Design
- **[🛡️ Quality & Testing](../../wiki/Quality-and-Testing.md)** — Strategy & Standards
- **[🛠️ Setup Guide](../../wiki/Setup.md)** — Configuration & Deployment
- **[📖 Glossary](../../wiki/glossary.md)** — Terminology

---

*Bruno Teixeira — ISEP / DevScope — 2025/2026*
