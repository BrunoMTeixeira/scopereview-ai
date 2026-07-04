import base64
import difflib
import html
import re
import httpx
import asyncio
from typing import Dict, List, Optional, Tuple

from ..core.logger import get_logger
from ..core.resilience import with_retry_on_transient_http_errors, with_fallback
from ..domain.triage import SKIP_EXTENSIONS

log = get_logger("ADO")


# Limit file downloads to 1MB to prevent OOM in the container
_MAX_FILE_DOWNLOAD_BYTES = 1024 * 1024


def _strip_html(text: str) -> str:
    """Strips HTML tags from ADO fields to save LLM tokens and improve reasoning."""
    if not text:
        return ""
    # Replace block-level tags with newlines to preserve some structure
    text = re.sub(r"<(br|/p|/div|/li|/h\d)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Unescape HTML entities
    text = html.unescape(text)
    # Clean up whitespace
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def _generate_numbered_diff(base_lines: List[str], target_lines: List[str], fromfile: str, tofile: str) -> str:
    """Generates a Unified Diff with absolute line numbers prepended to each line."""
    diff = difflib.unified_diff(base_lines, target_lines, fromfile=fromfile, tofile=tofile, n=5)
    numbered_lines = []

    cur_base = 0
    cur_target = 0

    for line in diff:
        if line.startswith("@@"):
            # Parse hunk header: @@ -start,len +start,len @@
            match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                cur_base = int(match.group(1))
                cur_target = int(match.group(2))
            numbered_lines.append(line)
            continue

        if line.startswith("---") or line.startswith("+++"):
            numbered_lines.append(line)
            continue

        if line.startswith("+"):
            # New content only increments target index
            numbered_lines.append(f"{cur_target:>4} | {line}")
            cur_target += 1
        elif line.startswith("-"):
            # Removed content only increments base index
            numbered_lines.append(f"{cur_base:>4} | {line}")
            cur_base += 1
        else:
            # Context line increments both
            numbered_lines.append(f"{cur_target:>4} | {line}")
            cur_base += 1
            cur_target += 1

    return "".join(numbered_lines)


class AzureDevOpsClient:
    """REST adapter for Azure DevOps Services API.

    This client implements the RepositoryClientPort and provides methods to
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
        self._client = httpx.AsyncClient(headers=self._ado_headers(), timeout=self._request_timeout)

    def _ado_headers(self) -> dict:
        token = base64.b64encode(f":{self._pat}".encode()).decode()
        return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    async def get_commit_head(self, repo_id: str, pr_id: int, project: str) -> str:
        """Returns the HEAD commit SHA of the Pull Request."""
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}?api-version=7.1"
        )
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return resp.json().get("lastMergeSourceCommit", {}).get("commitId", "")
        except httpx.RequestError as exc:
            log.error("Failed to get HEAD commit: %s", exc)
            return ""

    @with_retry_on_transient_http_errors(max_attempts=3)
    async def get_pr_details(self, repo_id: str, pr_id: int, project: str) -> Optional[dict]:
        """Returns title, description, commit_sha and base_sha of the PR.

        Automatically retries on transient failures (5xx, 429, network errors).
        """
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}?api-version=7.1"
        )
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return {
                "title": data.get("title", ""),
                "description": data.get("description", ""),
                "author": data.get("createdBy", {}).get("displayName", ""),
                "commit_sha": data.get("lastMergeSourceCommit", {}).get("commitId", ""),
                "base_sha": data.get("lastMergeTargetCommit", {}).get("commitId", ""),
            }
        except httpx.RequestError as exc:
            log.error("Failed to fetch PR details: %s", exc)
            return None

    async def get_file_content(self, repo_id: str, project: str, path: str, commit_sha: str) -> str:
        """Downloads the raw file at the given commit SHA."""
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/items"
            f"?path={path}&versionDescriptor.version={commit_sha}"
            f"&versionDescriptor.versionType=commit&api-version=7.1"
        )
        try:
            async with self._client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    return ""
                resp.raise_for_status()

                content_length = resp.headers.get("Content-Length")
                if content_length and int(content_length) > _MAX_FILE_DOWNLOAD_BYTES:
                    log.warning("File %s is too large (%s bytes), skipping download", path, content_length)
                    return f"[FILE TOO LARGE: {content_length} bytes]"

                await resp.aread()
                return resp.text
        except httpx.RequestError as exc:
            log.warning("Failed to read file %s at %s: %s", path, commit_sha, exc)
            return ""

    async def get_file_diff(self, repo_id: str, project: str, path: str, commit_sha: str) -> str:
        """Downloads the file at the given commit SHA and formats it with line numbers."""
        texto = await self.get_file_content(repo_id, project, path, commit_sha)
        if not texto:
            return ""
        linhas = texto.splitlines()[: self._max_lines]
        return "\n".join([f"{i + 1:>4} | {l}" for i, l in enumerate(linhas)])

    async def get_changed_files(
        self,
        repo_id: str,
        pr_id: int,
        project: str,
        commit_sha: str,
        base_sha: str = "",
    ) -> Tuple[Dict[str, str], Dict[str, str], int]:
        """Returns (mapa_full, mapa_diffs, total_eligible)."""
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/iterations?api-version=7.1"
        )
        mapa_full: Dict[str, str] = {}
        mapa_diffs: Dict[str, str] = {}
        try:
            resp = await self._client.get(url)
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
            resp_changes = await self._client.get(url_changes)
            resp_changes.raise_for_status()
            changes = resp_changes.json().get("changeEntries", [])

            eligible_changes = []
            for change in changes:
                change_type = change.get("changeType", "").lower()
                is_folder = change.get("item", {}).get("isFolder", False)

                if change_type in ("rename", "delete") or is_folder:
                    continue

                path = change.get("item", {}).get("path", "")
                if path and not any(path.lower().endswith(ext) for ext in SKIP_EXTENSIONS):
                    eligible_changes.append((change, path))

            total_eligible = len(eligible_changes)

            for change, path in eligible_changes[: self._max_files]:
                # Get RAW content truncated to max lines (for AST/Compressor)
                raw_source = await self.get_file_content(repo_id, project, path, commit_sha)
                if raw_source:
                    truncated_lines = raw_source.splitlines()[: self._max_lines]
                    conteudo_full_raw = "\n".join(truncated_lines)
                    mapa_full[path] = conteudo_full_raw
                else:
                    conteudo_full_raw = ""

                # Build content WITH line numbers for AI context / diff fallback
                conteudo_diff_formatted = ""
                if conteudo_full_raw:
                    conteudo_diff_formatted = "\n".join(
                        [f"{i + 1:>4} | {l}" for i, l in enumerate(truncated_lines)]
                    )

                if base_sha:
                    raw_base = await self.get_file_content(repo_id, project, path, base_sha)
                    raw_target = raw_source
                    base_lines = [l + "\n" for l in raw_base.splitlines()[: self._max_lines]]
                    target_lines = [l + "\n" for l in raw_target.splitlines()[: self._max_lines]]
                    diff_text = _generate_numbered_diff(base_lines, target_lines, fromfile=path, tofile=path)
                    if diff_text.strip():
                        mapa_diffs[path] = diff_text
                    else:
                        mapa_diffs[path] = conteudo_diff_formatted
                else:
                    mapa_diffs[path] = conteudo_diff_formatted

            return mapa_full, mapa_diffs, total_eligible
        except httpx.RequestError as exc:
            log.error("Failed to list changed files: %s", exc)
            return {}, {}, 0

    @with_retry_on_transient_http_errors(max_attempts=3)
    @with_fallback(fallback_value=[])
    async def get_work_items(self, repo_id: str, pr_id: int, project: str) -> List[dict]:
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
            resp_threads = await self._client.get(url_threads)
            resp_threads.raise_for_status()
            refs = resp_threads.json().get("value", [])

            for ref in refs:
                wi_url = ref.get("url")
                if not wi_url:
                    continue

                resp_wi = await self._client.get(
                    f"{wi_url}?$expand=relations&api-version=7.1"
                )
                if resp_wi.status_code == 200:
                    wi_data = resp_wi.json()
                    fields = wi_data.get("fields", {})
                    wi_list.append(
                        {
                            "id": wi_data.get("id"),
                            "title": fields.get("System.Title", ""),
                            "type": fields.get("System.WorkItemType", ""),
                            "description": _strip_html(fields.get("System.Description", "")),
                            "acceptance_criteria": _strip_html(
                                fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")
                            ),
                            "url": wi_data.get("_links", {}).get("html", {}).get("href", ""),
                        }
                    )
            return wi_list
        except httpx.RequestError as exc:
            log.error("Failed to fetch linked Work Items: %s", exc)
            return []

    async def get_repo_rules(self, repo_id: str, project: str, commit_sha: str) -> str:
        """Fetches .codereview.yml or .requirements.yml from the root."""
        for rule_file in [".codereview.yml", ".requirements.yml"]:
            url = (
                f"https://dev.azure.com/{self._organization}/{project}"
                f"/_apis/git/repositories/{repo_id}/items"
                f"?path=/{rule_file}&versionDescriptor.version={commit_sha}"
                f"&versionDescriptor.versionType=commit&api-version=7.1"
            )
            try:
                resp = await self._client.get(url)
                if resp.status_code == 200:
                    return resp.text
            except httpx.RequestError:
                pass
        return ""

    async def post_comment(self, repo_id: str, pr_id: int, project: str, comment: str) -> None:
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
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            log.info("Successfully posted comment to PR #%s", pr_id)
        except httpx.RequestError as exc:
            log.error("Failed to post PR comment: %s", exc)

    async def post_pr_status(self, repo_id: str, pr_id: int, project: str, state: str, description: str) -> None:
        """Publishes a Status Check on the Pull Request.


        Args:
            repo_id: The repository identifier.
            pr_id: The Pull Request numeric ID.
            project: The project name.
            state: The status state, e.g., 'succeeded', 'failed', 'error', 'pending'.
            description: A short description for the status check.
        """
        url = (
            f"https://dev.azure.com/{self._organization}/{project}"
            f"/_apis/git/repositories/{repo_id}/pullRequests/{pr_id}/statuses?api-version=7.1"
        )
        payload = {
            "state": state,
            "description": description,
            "context": {
                "name": "ScopeReview AI",
                "genre": "continuous-integration"
            }
        }
        log.debug("Posting PR status '%s' to PR #%s", state, pr_id)
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            log.info("Successfully posted PR status '%s' to PR #%s", state, pr_id)
        except httpx.RequestError as exc:
            log.error("Failed to post PR status: %s", exc)
