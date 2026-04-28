import pytest
from src.bootstrap import bootstrap_dependencies
from src.composition import get_pipeline_orchestrator
from src.core.di import injector
from src.services.orchestrator import PipelineOrchestrator

def test_bootstrap_initialization(env_setup):
    """
    Test Case: Bootstrap & DI Lifecycle
    Objectivo: Verificar se o bootstrapper consegue inicializar todas as dependências
    e se o contentor de DI entrega o Singleton correto.
    """
    # 1. Garantir contentor limpo
    injector.clear_all()
    
    # 2. Executar Bootstrap
    bootstrap_dependencies()
    
    # 3. Verificar se o Orchestrator foi registado
    orchestrator = get_pipeline_orchestrator()
    assert orchestrator is not None
    assert isinstance(orchestrator, PipelineOrchestrator)
    
    # 4. Verificar Singleton (mesma instância)
    orchestrator_again = get_pipeline_orchestrator()
    assert orchestrator is orchestrator_again
