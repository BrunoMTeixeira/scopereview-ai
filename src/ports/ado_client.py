from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class AzureDevOpsClientPort(Protocol):
    """
    Outbound port: PR / work items / comments (orchestrator use-case).
    File-level helpers stay on the concrete adapter only (ISP).
    """

    def get_pr_details(self, repo_id: str, pr_id: int, project: str) -> Optional[dict]: ...

    def get_changed_files(
        self,
        repo_id: str,
        pr_id: int,
        project: str,
        commit_sha: str,
        base_sha: str = "",
    ) -> Tuple[Dict[str, str], Dict[str, str], int]: ...

    def get_work_items(self, repo_id: str, pr_id: int, project: str) -> List[dict]: ...

    def get_repo_rules(self, repo_id: str, project: str, commit_sha: str) -> str: ...

    def post_comment(self, repo_id: str, pr_id: int, project: str, comment: str) -> None: ...
