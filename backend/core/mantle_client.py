"""Bedrock Mantle client (OpenAI-compatible chat completions with a bearer key).

Mantle serves Bedrock's open-weight catalogue and, unlike the Converse API, is
not blocked on new accounts. Plain urllib keeps it dependency-free; the payload
is small enough that a full SDK buys nothing here.
"""
import json
import urllib.error
import urllib.request

from core.http import ssl_context
from core.llm import LLMAPIError, LLMError, LLMResponseError, strip_fences
from core.settings import BEDROCK_API_KEY, BEDROCK_MANTLE_URL, MANTLE_MODEL_ID

DEFAULT_MAX_TOKENS = 1500
TIMEOUT_SECONDS = 60


class MantleClient:
    def __init__(self, model_id=None, api_key=None, base_url=None, max_tokens=DEFAULT_MAX_TOKENS,
                 opener=None):
        self.model_id = model_id or MANTLE_MODEL_ID
        self.api_key = api_key or BEDROCK_API_KEY
        self.base_url = (base_url or BEDROCK_MANTLE_URL).rstrip("/")
        if opener is None and not (self.api_key and self.base_url):
            raise LLMError("BEDROCK_API_KEY and BEDROCK_MANTLE_URL must be set")
        self.max_tokens = max_tokens
        self._opener = opener or self._http_post

    def _http_post(self, body: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=ssl_context()) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise LLMAPIError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMAPIError(str(e)) from e

    def converse_json(self, system: str, user: str, max_tokens=None):
        response = self._opener({
            "model": self.model_id,
            "temperature": 0,
            "max_tokens": max_tokens or self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        })

        choices = response.get("choices") or []
        if not choices:
            raise LLMResponseError("response contained no choices")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise LLMResponseError("response truncated at max_tokens; JSON would be incomplete")

        text = (choice.get("message") or {}).get("content") or ""
        if not text.strip():
            raise LLMResponseError("response contained no text content")

        try:
            return json.loads(strip_fences(text))
        except json.JSONDecodeError as e:
            raise LLMResponseError(f"malformed JSON: {e}") from e
