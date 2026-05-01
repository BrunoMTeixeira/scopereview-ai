import base64
import difflib
import requests
from typing import Dict, List, Optional, Tuple

from ..core.logger import get_logger
from ..core.resilience import with_retry_on_transient_http_errors, with_fallback

log = get_logger("ADO")

IGNORED_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".lock",
    ".yaml",
    ".yml",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".html",
    ".css",
    ".xml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
    ".gitignore",
}


# Limit file downloads to 1MB to prevent OOM in the container
_MAX_FILE_DOWNLOAD_BYTES = 1024 * 1024


class AzureDevOpsClient:
    """REST adapter for Azure DevOps Services API.

    This client implements the AzureDevOpsClientPort and provides methods to
    interact with PRs, files, work items, and threads.
    """

    def __init__(
        self,
        organization: str,
        pat: str,
        *,
        max_files: int = 5,
        max_lines: int = 400,
        request_timeout: int = 20,
    ):
        """Initializes the AzureDevOpsClient.

        Args:
            organization: The ADO organization name.
            pat: Personal Access Token for authentication.
            max_files: Limit for the number of files fetched per PR.
            max_lines: Limit for the number of lines fetched per file.
            request_timeout: HTTP request timeout in seconds.
        """
        self._organization = organization
        self._pat = pat
        self._max_files = max_files
        self._max_lines = max_lines
        self._request_timeout = request_timeout
        self._session = requests.Session()
        self._session.headers.update(self._ado_headers())

    def _ado_headers(self) -> dict:
        token = base64.b64encode(f":{self._pat}".encode()).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def get_commit_head(self, repo_id: str, pr_id: int, project: str) -> str:
        """Returns the HEAD commit SHA of the Pull Request."""
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}?api-version=7.1"
        )
        try:
            resp = self._session.get(url, timeout=self._request_timeout)
            resp.raise_for_status()
            return resp.json().get("lastMergeSourceCommit", {}).get("commitId", "")
        except requests.RequestException as exc:
            log.error("Failed to get HEAD commit: %s", exc)
            return ""

    @with_retry_on_transient_http_errors(max_attempts=3)
    def get_pr_details(self, repo_id: str, pr_id: int, project: str) -> Optional[dict]:
        """Returns title, description, commit_sha and base_sha of the PR.

        Automatically retries on transient failures (5xx, 429, network errors).
        """
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}?api-version=7.1"
        )
        try:
            resp = self._session.get(url, timeout=self._request_timeout)
            resp.raise_for_status()
            data = resp.json()
            return {
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "author": data.get("createdBy", {}).get("displayName", ""),
                "commit_sha": data.get("lastMergeSourceCommit", {}).get("commitId", ""),
                "base_sha": data.get("lastMergeTargetCommit", {}).get("commitId", ""),
            }
        except requests.RequestException as exc:
            log.error("Failed to fetch PR details: %s", exc)
            return None

    def get_file_content(self, repo_id: str, project: str, path: str, commit_sha: str) -> str:
        """Downloads the raw file at the given commit SHA."""
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/items"
            f"?path={path}&versionDescriptor.version={commit_sha}"
            f"&versionDescriptor.versionType=commit&api-version=7.1"
        )
        try:
            resp = self._session.get(url, timeout=self._request_timeout, stream=True)
            if resp.status_code == 404:
                return ""
            resp.raise_for_status()

            # Senior improvement: Check Content-Length before reading the full body
            content_length = resp.headers.get("Content-Length")
            if content_length and int(content_length) > _MAX_FILE_DOWNLOAD_BYTES:
                log.warning("File %s is too large (%s bytes), skipping download", path, content_length)
                return f"[FILE TOO LARGE: {content_length} bytes]"

            return resp.text
        except requests.RequestException as exc:
            log.warning("Failed to read file %s at %s: %s", path, commit_sha, exc)
            return ""

    def get_file_diff(self, repo_id: str, project: str, path: str, commit_sha: str) -> str:
        """Downloads the file at the given commit SHA and formats it with line numbers."""
        texto = self.get_file_content(repo_id, project, path, commit_sha)
        if not texto:
            return ""
        linhas = texto.splitlines()[: self._max_lines]
        return "\n".join([f"{i + 1:>4} | {l}" for i, l in enumerate(linhas)])

    def get_changed_files(
        self,
        repo_id: str,
        pr_id: int,
        project: str,
        commit_sha: str,
        base_sha: str = "",
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Returns (mapa_full, mapa_diffs)."""
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/iterations?api-version=7.1"
        )
        mapa_full: Dict[str, str] = {}
        mapa_diffs: Dict[str, str] = {}
        try:
            resp = self._session.get(url, timeout=self._request_timeout)
            resp.raise_for_status()
            iterations = resp.json().get("value") or []
            if not iterations:
                log.warning("PR #%s in project %s has no iterations (empty value[]).", pr_id, project)
                return {}, {}

            iter_id = iterations[-1]["id"]

            url_changes = (
                f"https://dev.azure.com/{self._organization}/{project}"
                f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/iterations/{iter_id}/changes?api-version=7.1"
            )
            resp_changes = self._session.get(url_changes, timeout=self._request_timeout)
            resp_changes.raise_for_status()
            changes = resp_changes.json().get("changeEntries", [])

            for change in changes[: self._max_files]:
                # Skip renames, deletions and folders (we only want clean code content)
                change_type = change.get("changeType", "").lower()
                is_folder = change.get("item", {}).get("isFolder", False)
                
                if change_type in ("rename", "delete") or is_folder:
                    continue
                
                path = change.get("item", {}).get("path", "")
                if path and not any(path.lower().endswith(ext) for ext in IGNORED_EXTENSIONS):
                    conteudo_full = self.get_file_diff(repo_id, project, path, commit_sha)
                    if conteudo_full:
                        mapa_full[path] = conteudo_full

                    if base_sha:
                        raw_base = self.get_file_content(repo_id, project, path, base_sha)
                        raw_target = self.get_file_content(repo_id, project, path, commit_sha)
                        base_lines = [l + "\n" for l in raw_base.splitlines()]
                        target_lines = [l + "\n" for l in raw_target.splitlines()]
                        diff = difflib.unified_diff(base_lines, target_lines, fromfile=path, tofile=path, n=5)
                        diff_text = "".join(diff)
                        if diff_text.strip():
                            mapa_diffs[path] = diff_text
                        else:
                            mapa_diffs[path] = conteudo_full
                    else:
                        mapa_diffs[path] = conteudo_full

            return mapa_full, mapa_diffs
        except requests.RequestException as exc:
            log.error("Failed to list changed files: %s", exc)
            return {}, {}

    @with_retry_on_transient_http_errors(max_attempts=3)
    @with_fallback(fallback_value=[])
    def get_work_items(self, repo_id: str, pr_id: int, project: str) -> List[dict]:
        """Fetches all work items linked to a Pull Request.

        Args:
            repo_id: The repository identifier.
            pr_id: The Pull Request numeric ID.
            project: The project name.

        Returns:
            List[dict]: A list of work item details (id, title, type, AC).

        Note:
            Returns empty list as fallback if all retries fail (non-critical for PR analysis).
        """
        url_threads = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/workitems?api-version=7.1"
        )
        wi_list: List[dict] = []
        try:
            resp_threads = self._session.get(url_threads, timeout=self._request_timeout)
            resp_threads.raise_for_status()
            refs = resp_threads.json().get("value", [])

            for ref in refs:
                wi_url = ref.get("url")
                if not wi_url:
                    continue

                resp_wi = self._session.get(
                    f"{wi_url}?$expand=relations&api-version=7.1",
                    timeout=self._request_timeout,
                )
                if resp_wi.status_code == 200:
                    wi_data = resp_wi.json()
                    fields = wi_data.get("fields", {})
                    wi_list.append(
                        {
                            "id": wi_data.get("id"),
                            "title": fields.get("System.Title", ""),
                            "type": fields.get("System.WorkItemType", ""),
                            "description": fields.get("System.Description", ""),
                            "acceptance_criteria": fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", ""),
                            "url": wi_data.get("_links", {}).get("html", {}).get("href", ""),
                        }
                    )
            return wi_list
        except requests.RequestException as exc:
            log.error("Failed to fetch linked Work Items: %s", exc)
            return []

    def get_repo_rules(self, repo_id: str, project: str, commit_sha: str) -> str:
        """Fetches .codereview.yml or .requirements.yml from the root."""
        for rule_file in [".codereview.yml", ".requirements.yml"]:
            url = (
                f"https://dev.azure.com/{self._organization}/{project}"
                f"/_apis/git/repositories/{repo_id}/items"
                f"?path=/{rule_file}&versionDescriptor.version={commit_sha}"
                f"&versionDescriptor.versionType=commit&api-version=7.1"
            )
            try:
                resp = self._session.get(url, timeout=self._request_timeout)
                if resp.status_code == 200:
                    return resp.text
            except requests.RequestException:
                pass
        return ""

    def post_comment(self, repo_id: str, pr_id: int, project: str, comment: str) -> None:
        """Publishes a summary thread comment on the Pull Request.
        
        Uses the ADO Threads API to create a new discussion thread.
        """
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/threads?api-version=7.1"
        )
        payload = {
            "comments": [{"parentCommentId": 0, "content": comment, "commentType": 1}],
            "status": 1,
        }
        log.debug("Posting thread to PR #%s (Project: %s, Length: %d chars)", pr_id, project, len(comment))
        try:
            resp = self._session.post(url, json=payload, timeout=self._request_timeout)
            resp.raise_for_status()
            log.info("Successfully posted comment to PR #%s", pr_id)
        except requests.RequestException as exc:
            log.error("Failed to post PR comment: %s", exc)
