import pytest

from core.llm import LLMAPIError, LLMError, LLMResponseError, get_client
from core.mantle_client import MantleClient


def make(text=None, finish="stop", error=None, choices=True):
    calls = []

    def opener(body):
        calls.append(body)
        if error:
            raise error
        if not choices:
            return {"choices": []}
        message = {"role": "assistant", "content": text}
        return {"choices": [{"finish_reason": finish, "message": message}]}

    return MantleClient(model_id="stub-model", opener=opener), calls


def test_parses_plain_and_fenced_json():
    assert make(text='{"ok": true}')[0].converse_json("s", "u") == {"ok": True}
    assert make(text='```json\n{"ok": true}\n```')[0].converse_json("s", "u") == {"ok": True}


def test_sends_system_and_user_as_chat_messages_at_temperature_zero():
    client, calls = make(text="{}")
    client.converse_json("SYS", "USR", max_tokens=99)
    body = calls[0]
    assert body["model"] == "stub-model"
    assert body["temperature"] == 0
    assert body["max_tokens"] == 99
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]


def test_truncated_response_raises_rather_than_partial():
    with pytest.raises(LLMResponseError):
        make(text='{"medicines": [', finish="length")[0].converse_json("s", "u")


def test_null_content_raises():
    with pytest.raises(LLMResponseError):
        make(text=None)[0].converse_json("s", "u")


def test_no_choices_raises():
    with pytest.raises(LLMResponseError):
        make(choices=False)[0].converse_json("s", "u")


def test_malformed_json_raises_typed_error():
    with pytest.raises(LLMResponseError):
        make(text="{not json")[0].converse_json("s", "u")


def test_http_failure_surfaces_as_api_error():
    with pytest.raises(LLMAPIError):
        make(error=LLMAPIError("HTTP 403"))[0].converse_json("s", "u")


def test_missing_credentials_fail_fast():
    with pytest.raises(LLMError):
        MantleClient(model_id="m", api_key="", base_url="")


def test_factory_knows_mantle(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "k")
    import core.mantle_client as mc

    monkeypatch.setattr(mc, "BEDROCK_API_KEY", "k")
    assert isinstance(get_client("mantle"), MantleClient)
