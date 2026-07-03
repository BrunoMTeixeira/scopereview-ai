"""
Ports Layer (Hexagonal Architecture / Ports & Adapters).

This package contains the abstract interfaces (Ports) required by the domain
and application services. By depending on these interfaces rather than concrete
implementations, the core logic remains decoupled from external infrastructure
such as APIs, databases, or specific LLM providers.
"""

from .repository_client import RepositoryClientPort
from .ai_client import AIModelClientPort
from .dedup import PipelineDedupPort

__all__ = ["AIModelClientPort", "RepositoryClientPort", "PipelineDedupPort"]
