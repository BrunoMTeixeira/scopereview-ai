# Changelog

## [1.0.0] — 2026-04

### Adicionado
- Webhook receiver para eventos `git.pullrequest.created` do Azure DevOps
- Extracção de conteúdo dos ficheiros alterados via REST API com numeração de linhas
- Integração com Azure AI Foundry (modelo Phi-4-mini-instruct)
- Publicação de comentários Markdown formatados no Pull Request
- Score de segurança (0-10) e recomendação de aprovação
- Deduplicação de PRs para evitar análises duplicadas em retries do ADO
- Suporte a múltiplas linguagens: Python, JavaScript, TypeScript, C#, Java, Go
- Endpoint `/health` para verificação de estado
- Containerização com Docker e Docker Compose
- Configuração via variáveis de ambiente (.env)
