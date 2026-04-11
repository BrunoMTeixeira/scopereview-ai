# poc-code-review

Agente de revisão automática de Pull Requests para o Azure DevOps,
suportado pelo modelo **Phi-4-mini-instruct** via **Azure AI Foundry**.

Quando um Pull Request é aberto ou actualizado, o agente é notificado
automaticamente, analisa o código alterado e publica comentários de revisão
estruturados directamente no PR — sem enviar código para servidores externos
fora do perímetro Microsoft Azure.

---

## Funcionalidades

- Detecção automática de Pull Requests via Webhooks do Azure DevOps
- Análise de código com LLM (Phi-4-mini-instruct) executado no Azure AI Foundry
- Publicação de comentários Markdown formatados no PR
- Score de segurança de 0 a 10 com recomendação de aprovação
- Identificação de SQL Injection, credenciais hardcoded, recursos não fechados, e outros problemas
- Sugestões de código corrigido para cada problema encontrado
- Deduplicação automática para evitar análises duplicadas em retries do ADO

---

## Pré-requisitos

- Python 3.12+
- Docker e Docker Compose
- Conta Azure com recurso Azure AI Foundry configurado
- Organização Azure DevOps com permissões de Service Hooks

---

## Configuração

### 1. Clonar o repositório

```bash
git clone <url-do-repositorio>
cd poc-code-review
```

### 2. Configurar variáveis de ambiente

```bash
cp .env.example .env
```

Edita o ficheiro `.env` com os valores reais:

| Variável | Descrição |
|---|---|
| `AZURE_ENDPOINT` | URL do endpoint Azure AI Foundry |
| `AZURE_MODEL` | Nome do modelo (ex: `Phi-4-mini-instruct`) |
| `AZURE_API_KEY` | Chave de API do recurso Azure AI Foundry |
| `ADO_ORGANIZATION` | Nome da organização no Azure DevOps |
| `ADO_PAT` | Personal Access Token do Azure DevOps |

O PAT necessita das seguintes permissões:
- `Code` → **Read**
- `Pull Request Threads` → **Read & Write**

### 3. Instalar dependências (desenvolvimento local)

```bash
pip install -r requirements.txt
```

---

## Execução

### Desenvolvimento local

```bash
# Terminal 1 — servidor
uvicorn src.agent:app --reload

# Terminal 2 — túnel público (necessário para receber Webhooks)
ngrok http 8000
```

### Docker

```bash
docker compose up --build
```

---

## Configurar o Webhook no Azure DevOps

1. Acede ao projecto no Azure DevOps
2. **Project Settings** → **Service Hooks** → **Create subscription**
3. Selecciona **Web Hooks** e clica **Next**
4. Trigger: `Pull request created` → **Next**
5. URL: `https://<url-publico>/webhook`
6. Clica **Test** para verificar a ligação e depois **Finish**

---

## Estrutura do projecto

```
poc-code-review/
├── src/
│   └── agent.py          # Lógica principal do agente
├── .env.example          # Modelo de variáveis de ambiente
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Endpoints

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/webhook` | Recebe eventos do Azure DevOps |
| `GET` | `/health` | Verificação de estado do agente |

---

## Limitações conhecidas

- Analisa no máximo 5 ficheiros por PR e 300 linhas por ficheiro
- Ficheiros de configuração e documentação são ignorados (`.md`, `.json`, `.yaml`, etc.)
- O URL do ngrok muda a cada reinício em desenvolvimento local — usar domínio estático ou deploy em Azure para persistência

---

## Desenvolvido no âmbito de

Estágio Curricular — Licenciatura em Engenharia Informática  
ISEP — Instituto Superior de Engenharia do Porto  
Entidade de Acolhimento: DevScope  
Ano Lectivo: 2025/2026
