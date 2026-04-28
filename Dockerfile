FROM python:3.12-slim

# Metadados
LABEL maintainer="Bruno Teixeira <1230741@isep.ipp.pt>"
LABEL description="poc-code-review — Agente de revisão automática de PRs no Azure DevOps"
LABEL version="1.0.0"

# Directório de trabalho
WORKDIR /app

# Instalar o curl para que o healthcheck do docker-compose funcione
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Instalar dependências primeiro (aproveita cache do Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fonte
COPY src/ ./src/

# Run as non-root user (security best practice)
RUN useradd --create-home appuser
USER appuser

# Expor porta
EXPOSE 8000

# Comando de arranque
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]