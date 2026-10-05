import httpx
from typing import Optional, Tuple

from ..core.llm_json import sanitize_llm_json_fragment
from ..core.logger import get_logger
from ..core.resilience import with_retry_on_transient_http_errors

log = get_logger("AzureAI")


class AzureOpenAIClient:
    """Azure OpenAI / Foundry chat adapter (implements AIModelClientPort)."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model_name: str,
        *,
        max_retries: int = 3,
        reasoning_effort: str = "low",
    ):
        self._endpoint = endpoint
        self._api_key = api_key
        self._model_name = model_name
        self._max_retries = max_retries
        self._reasoning_effort = reasoning_effort
        self._client = httpx.AsyncClient(timeout=180.0)

    def _build_payload(self, system_prompt: str, user_prompt: str, max_tokens: int) -> dict:
        """Constructs the LLM payload, dynamically handling O-series API constraints.

        O-series reasoning models (o1, o3-mini, o4-mini) do not support 'temperature',
        often reject the 'system' role, and require 'max_completion_tokens' instead
        of the traditional 'max_tokens'.
        """
        model_name = self._model_name
        if model_name.startswith("o") and (
            "-mini" in model_name or "o1" in model_name or "o3" in model_name or "o4" in model_name
        ):
            return {
                "model": model_name,
                "messages": [{"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}],
                "max_completion_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "reasoning_effort": self._reasoning_effort,
            }

        return {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "response_format": {"type": "json_object"}
        }

    @with_retry_on_transient_http_errors(max_attempts=3, min_wait=2, max_wait=20, retry_status_codes=(404, 429, 500, 502, 503, 504))
    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_tokens: int = 8000,
    ) -> Tuple[Optional[str], dict]:
        """
        Sends the prompt to Azure AI Foundry asynchronously.
        Handled by the @with_retry_on_transient_http_errors decorator for 429s/5xx.
        """
        headers = {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }

        payload = self._build_payload(system_prompt, user_prompt, max_tokens)

        resp = await self._client.post(self._endpoint, headers=headers, json=payload)
        if resp.is_error:
            log.error("Azure AI call to %s failed [%d]: %s", self._endpoint, resp.status_code, resp.text)
        resp.raise_for_status()

        data = resp.json()
        choice = data["choices"][0]
        raw_content = choice["message"]["content"]

        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Reasoning tokens specific to O-series models (o1, o3, o4)
        details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = details.get("reasoning_tokens", 0)

        usage_dict = {
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "reasoning_tokens": reasoning_tokens
        }

        finish_reason = choice.get("finish_reason")

        if finish_reason == "length":
            log.error(
                "CRITICAL: LLM response hit max token limit (finish_reason=length). "
                "The JSON analysis is likely truncated and may fail parsing. "
                "(total_tokens=%s).",
                total_tokens,
            )

        start = raw_content.find("{")
        end = raw_content.rfind("}")
        if start != -1 and end != -1:
            sanitized = sanitize_llm_json_fragment(raw_content[start : end + 1])
            return sanitized, usage_dict

        log.error("AI response did not contain a valid JSON block.")
        return None, usage_dict
