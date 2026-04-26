# ScopeReview AI — Automated PR Analysis for Azure DevOps

> Enterprise-grade hybrid pipeline system for automated **code review** and **requirements validation** in Azure DevOps Pull Requests, powered by Deterministic Static Analysis + Llama-3.3-70B-Instruct via Azure AI Foundry.

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)
[![Azure AI](https://img.shields.io/badge/Azure-AI_Foundry-0078D4?style=flat&logo=microsoftazure&logoColor=white)](https://ai.azure.com/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat)](LICENSE)

---

## What it does

When a developer creates or updates a Pull Request on Azure DevOps, the system automatically runs a sequential orchestrator:

1. **Code Review Agent (Phase 1: Static Checks)** — Deterministically scans for unhashed passwords, unused imports, PII log leaks, and unhandled DB rowcounts.
2. **Code Review Agent (Phase 2: LLM Analysis)** — AI analyzes the logical flow, security edge cases, and calculates a deterministic score.
3. **Requirements Validation Agent** — Takes the code findings as truth, cross-references with ADO Work Items, and verifies if business rules and acceptance criteria were actually implemented across the entire call graph.

Both reviews are published as structured Markdown comments in the PR, all within your Azure security perimeter.

```
Developer opens PR → ADO Webhook → Orchestrator Pipeline
                                        ↓
                                  1. Static Code Analysis + Code Review Agent
                                        ↓
                                  2. Requirements Validation Agent (Injects Code Findings)
                                        ↓
                                  2x Markdown Review Reports
```

---

## Quick Start

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [ngrok](https://ngrok.com/) — to expose your local app to Azure DevOps
- Azure account with [AI Foundry](https://ai.azure.com/) access
- Azure DevOps project with Git repositories

### 1️- Clone & Setup

```bash
git clone https://github.com/your-org/poc-code-review.git
cd poc-code-review
```

Create `.env` file:

```env
# Azure AI Foundry credentials
AZURE_ENDPOINT=https://<resource>.services.ai.azure.com/models/chat/completions?api-version=2024-05-01-preview
AZURE_MODEL=Llama-3.3-70B-Instruct
AZURE_API_KEY=<your-api-key>

# Azure DevOps credentials
ADO_ORGANIZATION=<your-org-name>
ADO_PAT=<personal-access-token>
```

> 💡 Get your API keys from [Azure AI Foundry](https://ai.azure.com) and Azure DevOps Personal Access Tokens.

### 2️- Run Locally

```bash
docker compose up --build
```

Verify it's working:

```bash
curl http://localhost:8000/health
# {"status":"ok","system":"ScopeReview AI","version":"1.0.0","agents":{"code_review":...}}
```

### 3️- Expose to Azure DevOps

In a new terminal:

```bash
ngrok http 8000
# Forwarding  https://xxxx-xxxx-xxxx.ngrok-free.app -> http://localhost:8000
```

### 4️- Configure Webhooks in Azure DevOps

Navigate to **Project Settings → Service Hooks** and create **ONE** subscription:

**Subscription — Orchestrator Pipeline**
- Event: "Pull request created/updated"
- URL: `https://<your-ngrok-url>/webhook/orchestrate`

*(The orchestrator will automatically trigger both the Code Review and Requirements Validation agents sequentially).*

---

## Project Structure

```
src/
├── main.py                       # FastAPI app & router mounting
├── code_review_agent.py          # Security & quality analysis agent
├── requirements_review_agent.py  # Business logic validation agent
└── shared_state.py               # Thread-safe PR deduplication cache

docker-compose.yml               # Container orchestration
Dockerfile                       # Python 3.12 + dependencies
requirements.txt                 # Python packages
.env                             # Secrets (git-ignored)
.codereview.yml                  # (Optional) Global repo rules
```

---

## Example: What the Reports Look Like

When a PR is analyzed, you'll see **two comments** in the PR thread:

### Code Review Report

```markdown
## ScopeReview AI | Code Review Report

| Security Score | `████████░░` **8.5/10** |
| Recommendation | **PASSED** — Ready for merge |

### Issues Requiring Attention

**1 · 🟠 HIGH · Security · `user_manager.py:275`**

```python
        user.password = md5(password.encode()).hexdigest()
```
> **Justification:** MD5 is cryptographically broken and should not be used for passwords.
> **Suggestion:** Use `bcrypt` or `Argon2` instead.
```

### Requirements Validation Report

```markdown
## ScopeReview AI | Requirements Validation Report

| Overall Verdict | **NEEDS WORK** — Some requirements failed |
| Progress        | `#######░░░` **7/10 (70%)** |

### ⚠️ Issues Requiring Attention

**❌ WI-12-AC-04** · Partial · `user_manager.py:163`

```python
        print("Data exported successfully.") 
```
> **Missing Detail:** The export_user_data method uses print instead of log.info on line 163. A concrete fix example would be to replace print with log.info.
```

---

## 🛠Configuration Options

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_FILES` | `5` | Maximum files to analyze per PR |
| `MAX_LINES` | `400` | Maximum lines per file |
| `MAX_TENTATIVAS` | `3` | Retry attempts on API errors |
| `MAX_HIGH_BLOCK`| `3` | Number of HIGH findings allowed before blocking PR |
| `DEDUP_SECONDS` | `300` | Duplicate PR detection window (seconds) |
| `WEBHOOK_SECRET`| `""` | Shared secret for webhook HTTP Basic Auth (optional) |
| `MAX_TOKEN_BUDGET`| `50000` | Limit on Azure AI tokens consumed per PR |

### Global Repository Rules (`.codereview.yml`)

Create `.codereview.yml` in your repo root to enforce organization-wide rules:

```yaml
# Example: Enforce type hints and docstrings
security_rules:
  - "All functions must include type hints"
  - "All public functions must have docstrings"

code_quality:
  - "Avoid hardcoding secrets; use environment variables"
  - "Maximum cyclomatic complexity: 10"
```

These rules will be checked on every PR.

---

## Deployment

### Production Deployment

For production, replace ngrok with:
- **Azure App Service** — Host the FastAPI app natively
- **Azure Container Instances** — Run containerized
- **Kubernetes** — Use `kubectl` for orchestration

Update your Azure DevOps webhooks to point to the production URL.

### Health Checks

```bash
# Overall system health (silent internal logging)
curl https://<your-domain>/health
```

---

## Performance & Cost

**Cost per PR:** ~€0.003 (using Llama-3.3-70B-Instruct)

For a team of 5 developers submitting **200 PRs/month**:
- **ScopeReview AI:** ~€1.20/month
- **CodeRabbit Pro:** ~€110/month  
- **GitHub Copilot:** ~€87/month

---

## Troubleshooting

### PR is not being analyzed

✅ Check webhook subscription is active in Azure DevOps  
✅ Verify ngrok tunnel is running and configured correctly  
✅ Check logs: `docker logs poc-code-review-app-1`

### Rate limit errors (429)

The system automatically retries with exponential backoff. If still failing:
- Reduce `MAX_FILES` or `MAX_LINES`
- Check your Azure AI Foundry quota

### JSON parsing errors

This is handled automatically. If persistent, check:
- Your `.env` file has correct `AZURE_ENDPOINT` and `AZURE_API_KEY`
- Model name matches Azure AI Foundry deployment

---

## Full Documentation

For detailed architecture, setup troubleshooting, and evaluation results, see the [Wiki](../../wiki):

- **[Architecture](../../wiki/Architecture)** — System design and agent interaction
- **[Setup Guide](../../wiki/Setup)** — Step-by-step configuration
- **[Development Log](../../wiki/Development-Log)** — Known issues and solutions
- **[Model Evaluation](../../wiki/Evaluation)** — Test results and metrics

---

## Contributing

This is a PoC developed during a BSc internship. For issues or suggestions:
1. Open an issue in this repository
2. Contact the project maintainers at DevScope

---

## License

MIT — See [LICENSE](LICENSE) file.

---

## Academic Context

**Project:** ScopeReview AI — Automated PR Analysis for Azure DevOps  
**Year:** 2025/2026  
**Student:** Bruno Teixeira  
**Institution:** ISEP — Instituto Superior de Engenharia do Porto  
**Company:** [DevScope](https://devscope.net)  
**Academic Supervisor:** Prof. Nuno Morgado (ISEP)  
**Company Supervisor:** Eng. David Mota (DevScope)

---

**Questions?** Check the [Wiki](../../wiki) or open an issue. 
