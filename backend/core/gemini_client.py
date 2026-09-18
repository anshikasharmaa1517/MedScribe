"""Gemini (Google AI Studio) client returning parsed JSON, mirroring BedrockClient.

Thinking is disabled: on 2.5 Flash it is on by default, its tokens count against
max_output_tokens, and it adds seconds of latency the 10-15s live loop can't spare.
"""
import json

from google import genai
from google.genai import errors, types

from core.llm import LLMAPIError, LLMError, LLMResponseError, strip_fences
from core.settings import GEMINI_API_KEY, GEMINI_MODEL_ID

DEFAULT_MAX_TOKENS = 1500


class GeminiClient:
    def __init__(self, model_id=None, api_key=None, max_tokens=DEFAULT_MAX_TOKENS, client=None):
        self.model_id = model_id or GEMINI_MODEL_ID
        api_key = api_key or GEMINI_API_KEY
        if client is None and not api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        self.max_tokens = max_tokens
        self._client = client or genai.Client(api_key=api_key)

    def converse_json(self, system: str, user: str, max_tokens=None):
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            max_output_tokens=max_tokens or self.max_tokens,
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            response = self._client.models.generate_content(
                model=self.model_id, contents=user, config=config
            )
        except errors.APIError as e:
            raise LLMAPIError(str(e)) from e

        candidates = response.candidates or []
        finish = getattr(candidates[0], "finish_reason", None) if candidates else None
        if finish is not None and finish.name == "MAX_TOKENS":
            raise LLMResponseError("response truncated at max_tokens; JSON would be incomplete")

        text = response.text or ""
        if not text.strip():
            raise LLMResponseError("response contained no text content")

        try:
            return json.loads(strip_fences(text))
        except json.JSONDecodeError as e:
            raise LLMResponseError(f"malformed JSON: {e}") from e
