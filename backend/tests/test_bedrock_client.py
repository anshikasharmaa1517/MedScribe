import pytest
from botocore.exceptions import ClientError

from core.bedrock_client import (
    BedrockAPIError,
    BedrockClient,
    BedrockError,
    BedrockResponseError,
    strip_fences,
)


class StubRuntime:
    def __init__(self, text=None, stop_reason="end_turn", error=None):
        self.text = text
        self.stop_reason = stop_reason
        self.error = error

    def converse(self, **_):
        if self.error:
            raise self.error
        content = [{"text": self.text}] if self.text is not None else []
        return {"stopReason": self.stop_reason, "output": {"message": {"content": content}}}


def make(**kwargs):
    return BedrockClient(model_id="stub-model", client=StubRuntime(**kwargs))


def test_strip_fences_handles_json_fence_and_plain_text():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('```\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fences('  {"a": 1}  ') == '{"a": 1}'


def test_parses_fenced_json():
    assert make(text='```json\n{"ok": true}\n```').converse_json("s", "u") == {"ok": True}


def test_malformed_json_raises_typed_error():
    with pytest.raises(BedrockResponseError):
        make(text="{not json").converse_json("s", "u")


def test_truncated_response_raises_rather_than_partial():
    with pytest.raises(BedrockResponseError):
        make(text='{"medicines": [', stop_reason="max_tokens").converse_json("s", "u")


def test_empty_content_raises():
    with pytest.raises(BedrockResponseError):
        make(text=None).converse_json("s", "u")


def test_api_failure_raises_typed_error():
    err = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Converse")
    with pytest.raises(BedrockAPIError):
        make(error=err).converse_json("s", "u")


def test_missing_model_id_fails_fast():
    with pytest.raises(BedrockError):
        BedrockClient(model_id="", client=StubRuntime())
