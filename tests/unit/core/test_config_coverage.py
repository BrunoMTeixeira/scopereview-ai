import os
import pytest
from pydantic import ValidationError
from src.core.config import Settings, load_settings

def test_config_production_https(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    
    with pytest.raises(ValidationError) as exc:
        Settings(AZURE_ENDPOINT_CR="http://not-https", AZURE_API_KEY_CR="valid", ADO_PAT="valid")
    assert "must use HTTPS" in str(exc.value)

    with pytest.raises(ValidationError) as exc:
        Settings(AZURE_ENDPOINT_CR="", AZURE_API_KEY_CR="valid", ADO_PAT="valid")
    assert "required in production" in str(exc.value)

    with pytest.raises(ValidationError) as exc:
        Settings(AZURE_ENDPOINT_CR="https://valid", AZURE_API_KEY_CR="", ADO_PAT="valid")
    assert "required in production" in str(exc.value)

def test_load_settings_fails_in_prod(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AZURE_API_KEY_CR", "")
    with pytest.raises(SystemExit):
        load_settings()

def test_config_properties(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    s = Settings(ADO_PAT="test_pat")
    assert s.is_dev is True
    assert s.is_production is False
    assert s.ado_auth_header.startswith("Basic ")
    
    s2 = Settings(ADO_PAT="")
    assert s2.ado_auth_header == ""
