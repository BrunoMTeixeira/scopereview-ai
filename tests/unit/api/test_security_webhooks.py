import pytest
from pydantic import ValidationError
from src.models.webhooks import ADOWebhookPayload

def test_webhook_payload_rejects_path_traversal():
    """Verifica se o modelo Pydantic rejeita nomes de projeto com caracteres de path traversal."""
    bad_payload = {
        "eventType": "git.pullrequest.created",
        "resourceVersion": "1.0",
        "resource": {
            "pullRequestId": 123,
            "status": "active",
            "title": "Hack PR",
            "repository": {
                "id": "repo-id",
                "name": "repo-name",
                "project": {
                    "name": "../../dangerous/path"  # Isto deve falhar
                }
            }
        }
    }
    
    with pytest.raises(ValidationError) as excinfo:
        ADOWebhookPayload(**bad_payload)
    
    assert "project" in str(excinfo.value)
    assert "name" in str(excinfo.value)
    print("\n[OK] Path traversal in project name successfully rejected by Pydantic Regex.")

def test_webhook_payload_accepts_valid_names():
    """Verifica se nomes válidos continuam a passar."""
    good_payload = {
        "eventType": "git.pullrequest.created",
        "resourceVersion": "1.0",
        "resource": {
            "pullRequestId": 123,
            "status": "active",
            "title": "Valid PR",
            "repository": {
                "id": "repo.id-123",
                "name": "repo-name",
                "project": {
                    "name": "My Project 1.0"
                }
            }
        }
    }
    payload = ADOWebhookPayload(**good_payload)
    assert payload.resource.repository.project.name == "My Project 1.0"
    print("\n[OK] Valid project and repo names successfully accepted.")
