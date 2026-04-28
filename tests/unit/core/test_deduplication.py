import pytest
import time
from src.core.pipeline_dedup import InMemoryPipelineDedup

def test_in_memory_dedup_blocks_duplicate():
    """Testa se o mesmo PR é bloqueado se enviado duas vezes seguidas no TTL."""
    # TTL de 60 segundos
    dedup = InMemoryPipelineDedup(ttl_seconds=60)
    
    # 1ª tentativa: deve deixar passar (False para skip)
    skip1 = dedup.should_skip_duplicate(123, "code-review")
    assert skip1 is False
    
    # 2ª tentativa imediata: deve mandar saltar (True para skip)
    skip2 = dedup.should_skip_duplicate(123, "code-review")
    assert skip2 is True

def test_in_memory_dedup_release():
    """Testa se a limpeza manual permite processar novamente."""
    dedup = InMemoryPipelineDedup(ttl_seconds=60)
    
    dedup.should_skip_duplicate(123, "code-review")
    # Limpar manualmente
    dedup.release(123, "code-review")
    
    # Deve deixar passar agora
    skip = dedup.should_skip_duplicate(123, "code-review")
    assert skip is False
