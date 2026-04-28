import random
import time
import requests
from typing import Optional, Tuple

from ..core.llm_json import sanitize_llm_json_fragment
from ..core.logger import get_logger
from ..core.resilience import with_retry_on_transient_http_errors

log = get_logger("AzureAI")


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter to reduce thundering herd on 429."""
    base = 2**attempt
    return base + random.uniform(0, base * 0.25)


class AzureOpenAIClient:
    """Azure OpenAI / Foundry chat adapter (implements AIModelClientPort)."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model_name: str,
        *,
        max_retries: int = 3,
    ):
        self._endpoint = endpoint
        self._api_key = api_key
        self._model_name = model_name
        self._max_retries = max_retries

    @with_retry_on_transient_http_errors(max_attempts=3, min_wait=2, max_wait=20)
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 8000,
    ) -> Tuple[Optional[str], int]:
        """
        Sends the prompt to Azure AI Foundry, handling retries, exponential backoff,
        and extracting the JSON string payload. Returns (content, total_tokens).

        This method is decorated with automatic retry logic for transient failures
        (429 rate limits, 5xx server errors, network timeouts).
        """
        headers = {
            "api-key": self._api_key,
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        model_name = self._model_name
        if model_name.startswith("o") and (
            "-mini" in model_name or "o1" in model_name or "o3" in model_name or "o4" in model_name
        ):
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}],
                "max_completion_tokens": max_tokens,
            }
        else:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.0,
            }

        for tentativa in range(1, self._max_retries + 1):
            try:
                resp = requests.post(self._endpoint, headers=headers, json=payload, timeout=180)
                if resp.status_code == 429:
                    wait_time = _backoff_seconds(tentativa)
                    log.warning(
                        "Rate limited (429). Retrying in %.1fs... (Attempt %s/%s)",
                        wait_time,
                        tentativa,
                        self._max_retries,
                    )
                    time.sleep(wait_time)
                    continue

                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                raw_content = choice["message"]["content"]
                total_tokens = data.get("usage", {}).get("total_tokens", 0)
                finish_reason = choice.get("finish_reason")
                if finish_reason == "length":
                    log.warning(
                        "LLM response hit max token limit (finish_reason=length); JSON may be truncated "
                        "(total_tokens=%s).",
                        total_tokens,
                    )

                start = raw_content.find("{")
                end = raw_content.rfind("}")
                if start != -1 and end != -1:
                    sanitized = sanitize_llm_json_fragment(raw_content[start : end + 1])
                    return sanitized, total_tokens

                log.error("AI response did not contain a valid JSON block.")
                return None, total_tokens

            except requests.RequestException as e:
                log.error("API Request failed on attempt %s: %s", tentativa, e)
                time.sleep(_backoff_seconds(tentativa))

        log.error("All AI retry attempts exhausted.")
        return None, 0
