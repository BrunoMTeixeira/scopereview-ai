from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class PullRequestReaderPort(Protocol):
    """
    Outbound port: Read PR metadata, code diffs, and requirements.
    """

    async def get_pr_details(self, repo_id: str, pr_id: int, project: str) -> Optional[dict]: ...

    async def get_changed_files(
        self,
        repo_id: str,
        pr_id: int,
        project: str,
        commit_sha: str,
        base_sha: str = "",
    ) -> Tuple[Dict[str, str], Dict[str, str], int]: ...

    async def get_work_items(self, repo_id: str, pr_id: int, project: str, pr_description: str = "") -> List[dict]: ...

    async def get_repo_rules(self, repo_id: str, project: str, commit_sha: str) -> str: ...


@runtime_checkable
class PullRequestWriterPort(Protocol):
    """
    Outbound port: Write comments and statuses to the PR.
    """

    async def post_comment(self, repo_id: str, pr_id: int, project: str, comment: str) -> None: ...

    async def post_pr_status(self, repo_id: str, pr_id: int, project: str, state: str, description: str) -> None: ...
