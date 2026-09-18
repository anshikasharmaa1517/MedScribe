"""Acceptance tests for process() from docs/BUILD_TASK_extraction_layer.md.

The LLM is stubbed with canned extraction JSON so these run offline and
deterministically; the resolver and validator are real.
"""
import pytest

from core.llm import LLMAPIError
from core.pipeline import process


class StubLLM:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def converse_json(self, system, user, max_tokens=None):
        if self.error:
            raise self.error
        return self.payload


def extraction(medicines, diagnosis=None, symptoms=(), tests=(), next_visit=None):
    return {
        "symptoms": list(symptoms),
        "diagnosis": diagnosis,
        "tests_advised": list(tests),
        "medicines": medicines,
        "next_visit": next_visit,
    }


def med(spoken, frequency=None, food_relation=None, duration=None):
    return {
        "spoken_name": spoken,
        "frequency": frequency,
        "food_relation": food_relation,
        "duration": duration,
    }


SAMPLE = extraction(
    symptoms=["fever", "body pain"],
    diagnosis="Viral fever",
    tests=["CBC"],
    medicines=[med("Dolo 650", "1-0-1", "after food", "5 days"), med("Cetzine 10", "0-0-1")],
    next_visit="5 days",
)


def run(payload, prior=None):
    return process("transcript", prior, client=StubLLM(payload))


def test_sample_consultation():
    s = run(SAMPLE)

    assert "fever" in s["symptoms"] and "body pain" in s["symptoms"]
    assert s["conditions_matched"] == ["Viral fever"]
    assert s["tests_advised"] == ["CBC"]
    assert s["next_visit"] == "5 days"
    assert len(s["medicines"]) == 2
    assert all(m["status"] == "AUTO" and m["clinical"] == "OK" for m in s["medicines"])
    dolo = s["medicines"][0]
    assert dolo["spoken"] == "Dolo 650"
    assert (dolo["frequency"], dolo["food_relation"], dolo["duration"]) == (
        "1-0-1", "after food", "5 days"
    )
    assert dolo["locked"] is False
    assert s["blocks_approval"] is False


def test_asr_mangle_resolves_to_azithral():
    s = run(extraction([med("azithril 500")]))
    m = s["medicines"][0]
    assert m["spoken"] == "azithril 500"
    assert m["matched"].startswith("Azithral 500")
    assert m["status"] == "AUTO"


def test_contradiction_downgrades_to_confirm():
    s = run(extraction([med("metrogyl 400")], diagnosis="sugar diabetes"))
    m = s["medicines"][0]
    assert m["clinical"] == "CONTRADICTS"
    assert m["status"] == "CONFIRM"


def test_no_diagnosis_is_pending_not_flagged():
    s = run(extraction([med("dolo 650"), med("metrogyl 400")]))
    assert s["conditions_matched"] == []
    assert all(m["clinical"] == "PENDING_CHECK" for m in s["medicines"])
    assert all(m["status"] == "AUTO" for m in s["medicines"])


def test_duplicate_salt_detected():
    s = run(extraction([med("dolo 650"), med("crocin 650")], diagnosis="viral fever"))
    assert s["duplicate_salts"]
    salt, brands = s["duplicate_salts"][0]
    assert salt.lower().startswith("paracetamol")
    assert len(brands) == 2


def test_doctor_edit_survives_reextraction():
    s1 = run(SAMPLE)
    s1["medicines"][0]["frequency"] = "1-1-1"
    s1["medicines"][0]["locked"] = True

    s2 = run(SAMPLE, prior=s1)
    dolo = next(m for m in s2["medicines"] if m["med_key"] == s1["medicines"][0]["med_key"])
    assert dolo["frequency"] == "1-1-1"
    assert dolo["locked"] is True


def test_unresolvable_name_blocks_approval():
    s = run(extraction([med("dolo 650"), med("zorblaxitron 900")], diagnosis="viral fever"))
    junk = s["medicines"][1]
    assert junk["status"] == "RESOLVE"
    assert junk["brand_id"] is None
    assert s["blocks_approval"] is True
    assert s["approval_blocked_by"] == [junk["med_key"]]


def test_medicine_persists_when_llm_stops_emitting_it():
    s1 = run(SAMPLE)
    s2 = run(extraction([med("Dolo 650", "1-0-1", "after food", "5 days")]), prior=s1)
    assert [m["spoken"] for m in s2["medicines"]] == ["Dolo 650", "Cetzine 10"]


def test_deleted_medicine_stays_deleted():
    s1 = run(SAMPLE)
    s1["medicines"][1]["deleted"] = True

    s2 = run(SAMPLE, prior=s1)
    cetzine = next(m for m in s2["medicines"] if m["spoken"] == "Cetzine 10")
    assert cetzine["deleted"] is True
    assert s2["duplicate_salts"] == []


def test_locked_diagnosis_not_overwritten():
    s1 = run(SAMPLE)
    s1["diagnosis"] = "dengue"
    s1["locked_fields"] = ["diagnosis"]

    s2 = run(SAMPLE, prior=s1)
    assert s2["diagnosis"] == "dengue"


def test_llm_failure_returns_prior_state():
    s1 = run(SAMPLE)
    err = LLMAPIError("throttled")
    s2 = process("transcript", s1, client=StubLLM(error=err))

    assert s2["medicines"] == s1["medicines"]
    assert s2["extraction_error"] == "throttled"


def test_llm_failure_with_no_prior_returns_empty_draft():
    s = process("transcript", None, client=StubLLM(error=LLMAPIError("down")))
    assert s["medicines"] == []
    assert s["blocks_approval"] is False
    assert "extraction_error" in s


def test_idempotent():
    assert run(SAMPLE) == run(SAMPLE)


@pytest.mark.parametrize("field", ["symptoms", "tests_advised"])
def test_list_fields_are_copies(field):
    s = run(SAMPLE)
    s[field].append("x")
    assert "x" not in SAMPLE[field]
