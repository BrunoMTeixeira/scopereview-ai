import pytest
from unittest.mock import patch
from src.bootstrap import bootstrap_dependencies
from src.core.config import Settings

def test_bootstrap_exception_handling():
    with patch("src.bootstrap.AzureOpenAIClient") as mock_client:
        mock_client.side_effect = Exception("Initialization failed")
        
        with pytest.raises(Exception):
            bootstrap_dependencies(Settings())
