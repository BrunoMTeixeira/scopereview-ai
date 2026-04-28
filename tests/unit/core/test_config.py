import pytest
from src.core.config import Settings

def test_settings_attributes():
    """Testa se os atributos existem e podem ser configurados."""
    settings = Settings()
    
    # Atribuir valores manualmente para testar a lógica do objeto
    settings.AZURE_API_KEY_CR = "secret-key"
    settings.ADO_PAT = "my-pat"
    
    assert settings.AZURE_API_KEY_CR == "secret-key"
    assert settings.ADO_PAT == "my-pat"

def test_settings_default_values():
    """Testa os valores por defeito definidos na classe."""
    settings = Settings()
    # Verificar se os nomes dos atributos estão corretos e têm valores coerentes
    assert hasattr(settings, "MAX_FILES")
    assert hasattr(settings, "MAX_TOKEN_BUDGET")
    assert settings.MAX_FILES > 0
