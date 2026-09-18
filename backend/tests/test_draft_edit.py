import pytest

from core.draft_edit import PatchError, apply_patch
from core.pipeline import process
from core.reference import default_reference
from tests.test_pipeline import SAMPLE, StubLLM, extraction, med


def base_state():
    return process("t", None, client=StubLLM(SAMPLE))


def test_field_edit_locks_field():
    s = apply_patch(base_state(), {"field": "diagnosis", "value": "dengue"})
    assert s["diagnosis"] == "dengue" and s["locked_fields"] == ["diagnosis"]
    assert s["conditions_matched"] == []  # revalidated against the new diagnosis


def test_list_field_edit_cleans_items():
    s = apply_patch(base_state(), {"field": "symptoms", "value": [" fever ", "", "cough"]})
    assert s["symptoms"] == ["fever", "cough"] and "symptoms" in s["locked_fields"]


def test_dose_edit_locks_medicine_only():
    s = apply_patch(base_state(), {"medicine": {"med_key": "B001", "frequency": "1-1-1"}})
    dolo = next(m for m in s["medicines"] if m["med_key"] == "B001")
    assert dolo["frequency"] == "1-1-1" and dolo["locked"] is True
    assert all(not m["locked"] for m in s["medicines"] if m["med_key"] != "B001")


def test_delete_is_tombstone_not_removal():
    s = apply_patch(base_state(), {"medicine": {"med_key": "B025", "deleted": True}})
    assert [m["med_key"] for m in s["medicines"]] == ["B001", "B025"]
    assert next(m for m in s["medicines"] if m["med_key"] == "B025")["deleted"] is True


def test_resolve_by_brand_sets_tier0_fields_and_clears_block():
    s0 = process("t", None, client=StubLLM(extraction([med("zorblaxitron 900")], "viral fever")))
    junk = s0["medicines"][0]
    assert s0["blocks_approval"] and junk["status"] == "RESOLVE"

    s = apply_patch(s0, {"medicine": {"med_key": junk["med_key"], "brand_id": "B016"}})
    m = s["medicines"][0]
    assert (m["brand_id"], m["matched"], m["salt_ids"]) == ("B016", "Zerodol 100mg", ["S004"])
    assert m["status"] == "AUTO" and m["locked"] and m["spoken"] == "zorblaxitron 900"
    assert s["blocks_approval"] is False


def test_confirm_upgrades_confirm_to_auto_but_not_resolve():
    s0 = process("t", None, client=StubLLM(extraction([med("metrogyl 400")], "sugar diabetes")))
    assert s0["medicines"][0]["status"] == "CONFIRM"
    s = apply_patch(s0, {"medicine": {"med_key": "B073", "confirm": True}})
    assert s["medicines"][0]["status"] == "AUTO" and s["medicines"][0]["clinical"] == "CONTRADICTS"

    s1 = process("t", None, client=StubLLM(extraction([med("zorblaxitron 900")])))
    with pytest.raises(PatchError):
        apply_patch(s1, {"medicine": {"med_key": s1["medicines"][0]["med_key"], "confirm": True}})


def test_add_medicine_and_duplicate_guard():
    s = apply_patch(base_state(), {"medicine": {"add": {"brand_id": "B006", "frequency": "OD"}}})
    added = s["medicines"][-1]
    assert added["matched"] == "Crocin 650mg" and added["locked"] and added["frequency"] == "OD"
    assert s["duplicate_salts"] and s["duplicate_salts"][0][0].lower().startswith("paracetamol")
    with pytest.raises(PatchError):
        apply_patch(s, {"medicine": {"add": {"brand_id": "B006"}}})


def test_locked_edits_survive_next_extraction_pass():
    s = apply_patch(base_state(), {"medicine": {"med_key": "B001", "duration": "3 days"}})
    s = apply_patch(s, {"field": "next_visit", "value": "2 weeks"})
    s2 = process("t", s, client=StubLLM(SAMPLE))
    assert next(m for m in s2["medicines"] if m["med_key"] == "B001")["duration"] == "3 days"
    assert s2["next_visit"] == "2 weeks"


@pytest.mark.parametrize("patch", [
    {}, {"field": "brand_id", "value": 1}, {"field": "symptoms", "value": "x"},
    {"medicine": {"med_key": "nope", "deleted": True}}, {"medicine": {"med_key": "B001"}},
    {"medicine": {"add": {"brand_id": "B999"}}},
])
def test_bad_patches_raise(patch):
    with pytest.raises(PatchError):
        apply_patch(base_state(), patch, default_reference())


def test_patch_does_not_mutate_input():
    s0 = base_state()
    apply_patch(s0, {"medicine": {"med_key": "B001", "deleted": True}})
    assert s0["medicines"][0]["deleted"] is False
