"""Provider-agnostic LLM surface: one exception family, one factory.

The extractor only ever calls `client.converse_json(system, user)`. Which
provider answers is an env-var decision (`LLM_PROVIDER`), so Bedrock and
Gemini are interchangeable without touching extraction or pipeline code.
"""
import re

from core.settings import LLM_PROVIDER

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMError(Exception):
    """Base class for every error raised by an LLM client."""


class LLMAPIError(LLMError):
    """The provider call failed (auth, throttling, model access, network)."""


class LLMResponseError(LLMError):
    """The provider answered, but the content is not usable JSON."""


def strip_fences(text: str) -> str:
    match = _FENCE.search(text)
    return (match.group(1) if match else text).strip()


def get_client(provider: str | None = None):
    provider = provider or LLM_PROVIDER
    if provider == "gemini":
        from core.gemini_client import GeminiClient

        return GeminiClient()
    if provider == "bedrock":
        from core.bedrock_client import BedrockClient

        return BedrockClient()
    if provider == "mantle":
        from core.mantle_client import MantleClient

        return MantleClient()
    raise LLMError(f"unknown LLM_PROVIDER {provider!r}; expected 'gemini', 'bedrock' or 'mantle'")
