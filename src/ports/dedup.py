from typing import Protocol, runtime_checkable


@runtime_checkable
class PipelineDedupPort(Protocol):
    """
    Outbound port (application boundary): decide if a PR pipeline run should be skipped
    as a duplicate of a recent run. Implementations may use memory, Redis, etc.
    """

    def should_skip_duplicate(self, pr_id: int, agent: str = "default") -> bool:
        """Return True if this run should be skipped (already active / recently completed)."""

    def release(self, pr_id: int, agent: str = "default") -> None:
        """Clear the slot (e.g. after failure) so a retry can proceed."""
