"""Thin wrapper over the Bedrock Runtime converse API that returns parsed JSON.

Every failure surfaces as a typed exception. Callers never receive partial or
malformed output — the extractor relies on that to fall back to its previous
good state instead of corrupting the draft.
"""
import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from core.llm import LLMAPIError, LLMError, LLMResponseError, strip_fences  # noqa: F401
from core.settings import AWS_REGION, BEDROCK_MODEL_ID

DEFAULT_MAX_TOKENS = 1500


class BedrockError(LLMError):
    """Base class for every error raised by this module."""


class BedrockAPIError(BedrockError, LLMAPIError):
    """The call to Bedrock failed (auth, throttling, model access, network)."""


class BedrockResponseError(BedrockError, LLMResponseError):
    """Bedrock answered, but the content is not usable JSON."""


class BedrockClient:
    def __init__(self, model_id=None, region=None, max_tokens=DEFAULT_MAX_TOKENS, client=None):
        self.model_id = model_id or BEDROCK_MODEL_ID
        if not self.model_id:
            raise BedrockError("BEDROCK_MODEL_ID is not set")
        self.max_tokens = max_tokens
        self._client = client or boto3.client("bedrock-runtime", region_name=region or AWS_REGION)

    def converse_json(self, system: str, user: str, max_tokens=None):
        try:
            response = self._client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": max_tokens or self.max_tokens, "temperature": 0},
            )
        except (ClientError, BotoCoreError) as e:
            raise BedrockAPIError(str(e)) from e

        if response.get("stopReason") == "max_tokens":
            raise BedrockResponseError("response truncated at max_tokens; JSON would be incomplete")

        blocks = response.get("output", {}).get("message", {}).get("content", [])
        text = "".join(b["text"] for b in blocks if "text" in b)
        if not text.strip():
            raise BedrockResponseError("response contained no text content")

        try:
            return json.loads(strip_fences(text))
        except json.JSONDecodeError as e:
            raise BedrockResponseError(f"malformed JSON: {e}") from e
