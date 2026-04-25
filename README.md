# ScopeReview AI (PoC)

> Enterprise-grade Automated Pull Request Agent for Azure DevOps, powered by LLMs via Azure AI Foundry.
> Features a Dual-Agent architecture for both **Code Quality** and **Business Requirements** validation.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)
[![Azure](https://img.shields.io/badge/Azure-AI_Foundry-0078D4?style=flat&logo=microsoftazure&logoColor=white)](https://ai.azure.com/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat)](LICENSE)

---

## What this does

When a developer opens a Pull Request on Azure DevOps, the system triggers a **Dual-Agent Pipeline**:

1. **Code Review Agent:** Fetches changed files, splits them into logical blocks, and analyses them for security
   vulnerabilities, bugs, and code quality issues.
2. **Requirements Validation Agent:** Fetches linked Azure DevOps Work Items (Acceptance Criteria) and any global
   repository rules (`.codereview.yml`), cross-referencing them against the submitted code to ensure business logic is
   fully implemented.

Both agents publish structured Markdown reports directly into the PR. The entire pipeline runs inside the Azure security
perimeter — code never reaches external servers.

```text
Developer opens PR → ADO Webhook → FastAPI App → Azure AI Foundry (Llama 3.3)
                                         │                  │
                                    ADO REST API  ←─────────┘
                                         │
                                  2x Review Reports
                                  published in PR
```

-----

## Key Features & Resilience

- **Dual-Agent Architecture:** Separates concerns between code syntax/security and business logic validation.
- **Repository Rule Enforcement:** Automatically detects and enforces global architecture rules defined in
  `.codereview.yml`.
- **Fair Grading System:** PRs are only blocked (`ACTION REQUIRED`) if they contain `CRITICAL` vulnerabilities, more
  than 3 `HIGH` severity issues, or drop below a `7/10` security score.
- **Enterprise-Grade Resilience:** Implements **Exponential Backoff with Jitter** to handle Azure API rate limits (429
  Too Many Requests) smoothly during massive PRs.
- **Crash-Proof Parsing:** Uses an advanced Array-String chunking strategy to guarantee 100% JSON compliance from the
  LLM, preventing parsing crashes on complex code snippets.

-----

## Example outputs

### 🛡️ Code Review Report

```markdown
## ScopeReview AI | Code Review Report
---

### Overview

| Metric | Value |
|---|---|
| Security Score | `█████████░` **9/10** |
| Recommendation | **PASSED** — *Ready for merge* |
| Total Issues | 2 |

### Findings Index

| ID | Severity | Category | Location | Title |
|:---|:---|:---|:---|:---|
| 01 | `HIGH` | SECURITY | `user_manager.py:94` | Insecure Password Hashing |
| 02 | `LOW` | QUALITY | `user_manager.py:8` | Unused Import (pprint) |
```

### 📋 Requirements Validation Report

```markdown
## ScopeReview AI | Requirements Validation Report
---

### Overview

| Metric | Value |
|---|---|
| Overall Verdict | **APPROVED** — *All verifiable requirements are implemented* |
| Implementation Progress | `##########` **15/15 (100%)** |

### Implemented Requirements

* **WI-7-REQ-06** — Deve existir uma função anonymize_user(user_id) para cumprir o 'Direito ao Esquecimento'
* **RULE-03** — Every new function declaration must include Python type hints
  ...
```

-----

## Quick start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [ngrok](https://ngrok.com/) (to expose localhost to Azure DevOps)
- Azure account with AI Foundry access
- Azure DevOps repository

### 1. Clone and configure

```bash
git clone [https://github.com/](https://github.com/)<your-user>/poc-code-review.git
cd poc-code-review
```

Create a `.env` file in the project root:

```env
AZURE_ENDPOINT=https://<resource>[.services.ai.azure.com/models/chat/completions?api-version=2024-05-01-preview](https://.services.ai.azure.com/models/chat/completions?api-version=2024-05-01-preview)
AZURE_MODEL=Llama-3.3-70B-Instruct
AZURE_API_KEY=<your-api-key>

ADO_ORGANIZATION=<your-org-id>
ADO_PAT=<your-personal-access-token>
```

### 2. Run

```bash
docker compose up --build
```

Verify the system is running:

```bash
curl http://localhost:8000/health/code-review
# {"status":"ok","agent":"Code Review Agent","model":"Llama-3.3-70B-Instruct"}
```

### 3. Expose to the internet

```bash
ngrok http 8000
# Forwarding  [https://xxxx.ngrok-free.app](https://xxxx.ngrok-free.app) -> http://localhost:8000
```

### 4. Configure the webhooks in Azure DevOps

```text
ADO Project → Project Settings → Service Hooks → Create 2 subscriptions
  Trigger 1:  Pull request created/updated -> URL: https://<your-ngrok-url>/webhook
  Trigger 2:  Pull request created/updated -> URL: https://<your-ngrok-url>/webhook/requirements
```

-----

## Project structure

```text
poc-code-review/
├── src/
│   ├── main.py                        # FastAPI entry point & router mounting
│   ├── code_review_agent.py           # Agent 1: Security & Quality
│   └── requirements_review_agent.py   # Agent 2: Business Logic & Work Items
├── .codereview.yml               # (Optional) Global repo rules to enforce
├── docker-compose.yml            # container orchestration
├── Dockerfile                    # container image definition
├── requirements.txt              # Python dependencies
├── .env                          # secrets (git-ignored)
└── README.md
```

-----

## Cost-Efficiency (Based on Real Telemetry)

Running this dual-agent system is astronomically more cost-effective than commercial alternatives. Based on real
telemetry data from Azure AI Foundry (Llama-3.3-70B-Instruct), the average cost per AI request is **~€0.00045** (
averaging 886 tokens/request).

For a team of 5 developers submitting **200 PRs/month** — assuming an average of 10 AI requests per PR to safely cover
both code chunking and requirements validation — the total running cost is estimated at **under €1.50/month**.

| Solution                       | Monthly cost (5 devs, 200 PRs) |
|--------------------------------|--------------------------------|
| **ScopeReview AI (Llama 70B)** | **~ € 1.50**                   |
| CodeRabbit Pro                 | ~ € 110.00 ($120)              |
| GitHub Copilot                 | ~ € 87.00 ($95)                |

> *Note: ScopeReview AI cost projection is derived from baseline PoC testing where 265 requests (183.7k input tokens /
51.1k output tokens) generated a total billing of exactly €0.12.*
-----

## Known limitations

- **No inline line-level comments** — reviews are currently published as a single aggregated PR comment thread, not
  attached to individual diff lines.
- **Diff Context Isolation** — The agent currently pulls the full file (up to max lines) rather than just the strictly
  modified diff hunks, which may result in reviewing untouched code.

-----

## Documentation

| Page                                          | Description                                   |
|-----------------------------------------------|-----------------------------------------------|
| [Home](../../wiki)                            | Project overview and navigation               |
| [Setup](../../wiki/Setup)                     | Step-by-step installation and configuration   |
| [Architecture](../../wiki/Architecture)       | Technical decisions and system design         |
| [Development Log](../../wiki/Development-Log) | Problems encountered and how they were solved |
| [Model Selection](../../wiki/Model-Selection) | LLM comparison and cost analysis              |
| [Evaluation](../../wiki/Evaluation)           | Test results and detection metrics            |

-----

## Academic context

This project was developed as a Proof of Concept during a curricular internship at [DevScope](https://devscope.net), as
part of the BSc in Computer Engineering at ISEP — Instituto Superior de Engenharia do Porto.

**Internship year:** 2025/2026  
**Student:** Bruno Teixeira  
**Academic supervisor:** Prof. Nuno Morgado (ISEP)  
**Company supervisor:** Eng. David Mota (DevScope)
