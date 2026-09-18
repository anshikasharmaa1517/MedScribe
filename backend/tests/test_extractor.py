"""Unit tests for core.extractor against the sample transcript in
docs/BUILD_TASK_extraction_layer.md.

Bedrock is mocked at the boto3 `converse` boundary, so BedrockClient's real
parsing runs and the prompt/messages the extractor sends can be inspected.
The model's answer is canned - these tests pin the contract around the call,
not the model's behaviour.
"""
import json

import pytest

from core import extractor
from core.bedrock_client import BedrockClient
from core.llm import LLMResponseError
from core.settings import EXTRACT_MAX_TOKENS

SAMPLE_TRANSCRIPT = """Doctor: Namaste, kya problem hai?
Patient: Sir, do din se bukhar hai aur body pain bhi hai.
Doctor: Temperature check karte hain... 101 hai. Viral fever lag raha hai.
        CBC karwa lena. Dolo 650 le lena, ek subah ek shaam, khaane ke baad,
        paanch din. Aur Cetzine 10 raat ko. Paanch din baad dikha dena."""

SAMPLE_RESPONSE = {
    "symptoms": ["fever", "body pain"],
    "diagnosis": "Viral fever",
    "tests_advised": ["CBC"],
    "medicines": [
        {
            "spoken_name": "Dolo 650",
            "frequency": "1-0-1",
            "food_relation": "after food",
            "duration": "5 days",
        },
        {"spoken_name": "Cetzine 10", "frequency": "0-0-1", "food_relation": None,
         "duration": None},
    ],
    "next_visit": "5 days",
}


class StubRuntime:
    """Stands in for boto3's bedrock-runtime client; records the converse kwargs."""

    def __init__(self, payload):
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": self.text}]}},
        }


def bedrock(payload):
    runtime = StubRuntime(payload)
    return BedrockClient(model_id="stub-model", client=runtime), runtime


# --- the sample consultation -------------------------------------------------


def test_sample_transcript_extracts_expected_fields():
    client, _ = bedrock(SAMPLE_RESPONSE)
    out = extractor.extract(SAMPLE_TRANSCRIPT, client=client)

    assert "fever" in out["symptoms"] and "body pain" in out["symptoms"]
    assert out["diagnosis"] == "Viral fever"
    assert out["tests_advised"] == ["CBC"]
    assert out["next_visit"] == "5 days"
    assert len(out["medicines"]) == 2
    dolo, cetzine = out["medicines"]
    assert dolo == {
        "spoken_name": "Dolo 650",
        "frequency": "1-0-1",
        "food_relation": "after food",
        "duration": "5 days",
    }
    assert cetzine["spoken_name"] == "Cetzine 10"
    assert cetzine["frequency"] == "0-0-1"
    assert cetzine["duration"] is None  # Dolo's "paanch din" must not leak across


def test_sends_prompt_file_and_verbatim_transcript_to_bedrock():
    client, runtime = bedrock(SAMPLE_RESPONSE)
    extractor.extract(SAMPLE_TRANSCRIPT, client=client)

    (call,) = runtime.calls
    assert call["system"] == [{"text": extractor.system_prompt()}]
    assert SAMPLE_TRANSCRIPT in call["messages"][0]["content"][0]["text"]
    assert call["inferenceConfig"]["maxTokens"] == EXTRACT_MAX_TOKENS
    assert call["inferenceConfig"]["temperature"] == 0


def test_extract_uses_configured_client_when_none_given(monkeypatch):
    client, runtime = bedrock(SAMPLE_RESPONSE)
    monkeypatch.setattr(extractor, "get_client", lambda: client)

    out = extractor.extract(SAMPLE_TRANSCRIPT)

    assert out["diagnosis"] == "Viral fever"
    assert len(runtime.calls) == 1


def test_handles_fenced_json_from_model():
    client, _ = bedrock("```json\n" + json.dumps(SAMPLE_RESPONSE) + "\n```")
    assert extractor.extract(SAMPLE_TRANSCRIPT, client=client)["tests_advised"] == ["CBC"]


# --- the prompt file ---------------------------------------------------------


def test_prompt_file_carries_the_critical_rules():
    p = extractor.system_prompt()
    assert "VERBATIM" in p
    assert '"azithril 500" stays "azithril 500"' in p
    assert "Never output a medicine ID, brand ID" in p
    assert "Never invent" in p
    for hinglish, notation in [
        ("ek subah ek shaam", "1-0-1"),
        ("khaane ke baad", "after food"),
        ("paanch din", "5 days"),
    ]:
        assert hinglish in p and notation in p
    for shorthand in ("OD", "BD", "TDS", "QID", "SOS", "HS", "stat", "1-1-1"):
        assert shorthand in p


def test_prompt_stays_tight_for_the_live_loop():
    # Runs every 10-15s; a rough token proxy so the prompt can't quietly bloat.
    assert len(extractor.system_prompt().split()) < 450


def test_user_message_wraps_transcript_unchanged():
    msg = extractor.build_user_message("azithril 500 le lena")
    assert "azithril 500 le lena" in msg


# --- defensive parse ---------------------------------------------------------


def test_spoken_name_is_passed_through_untouched():
    client, _ = bedrock({"medicines": [{"spoken_name": "azithril 500"}]})
    out = extractor.extract("azithril 500 le lena", client=client)
    assert out["medicines"][0]["spoken_name"] == "azithril 500"


def test_missing_keys_are_filled_with_empties():
    client, _ = bedrock({})
    out = extractor.extract("Namaste", client=client)
    assert out == {
        "symptoms": [],
        "diagnosis": None,
        "tests_advised": [],
        "medicines": [],
        "next_visit": None,
    }


def test_model_emitted_identifiers_are_stripped():
    client, _ = bedrock(
        {"medicines": [{"spoken_name": "Dolo 650", "brand_id": "B001", "salt_ids": ["S001"]}]}
    )
    (med,) = extractor.extract("Dolo 650", client=client)["medicines"]
    assert "brand_id" not in med and "salt_ids" not in med
    assert set(med) == {"spoken_name", "frequency", "food_relation", "duration"}


@pytest.mark.parametrize(
    "payload",
    [
        [],  # not an object
        {"medicines": {"spoken_name": "Dolo 650"}},  # not a list
        {"medicines": ["Dolo 650"]},  # item not an object
        {"medicines": [{"frequency": "1-0-1"}]},  # no spoken_name
        {"medicines": [{"spoken_name": ""}]},  # blank spoken_name
        {"medicines": [{"spoken_name": "Dolo 650", "duration": 5}]},  # wrong type
        {"symptoms": [{"name": "fever"}]},  # list item not a string
        {"diagnosis": ["Viral fever"]},  # scalar given as list
    ],
)
def test_malformed_shape_raises_instead_of_partial_output(payload):
    # The glue relies on this to keep the previous good state (fail-safe rule).
    with pytest.raises(LLMResponseError):
        extractor.normalise(payload)


def test_bare_string_for_list_field_is_coerced_not_fatal():
    # Seen live from gpt-oss-120b: "tests_advised": "CBC". A shape slip must not
    # freeze the draft for the rest of the consult.
    out = extractor.normalise({"tests_advised": "CBC", "symptoms": "fever, body pain"})
    assert out["tests_advised"] == ["CBC"] and out["symptoms"] == ["fever", "body pain"]


def test_blank_strings_become_null_and_empty_list_items_are_dropped():
    out = extractor.normalise(
        {"diagnosis": "", "symptoms": ["fever", "", "  "], "next_visit": None}
    )
    assert out["diagnosis"] is None
    assert out["symptoms"] == ["fever"]
