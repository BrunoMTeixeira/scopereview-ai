from typing import Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class AIModelClientPort(Protocol):
    """Outbound port: chat completion for code/requirements agents."""

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 8000,
    ) -> Tuple[Optional[str], dict]:
        """Returns (json_text_or_none, usage_metrics_dict)."""
        ...
