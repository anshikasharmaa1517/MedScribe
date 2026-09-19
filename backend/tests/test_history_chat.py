from core import history_chat
from tests.test_pipeline import StubLLM

PATIENT = {"name": "Ramesh Iyer", "age": 54, "sex": "M", "chronicConditions": ["Hypertension"]}
RXS = [
    {"rxId": "r1", "doctorId": "d1", "createdAt": "2026-08-01T10:00:00Z",
     "diagnosis": "hypertension",
     "medicines": [{"label": "Amlong 5mg", "frequency": "1-0-0", "food_relation": "after food",
                    "duration": "30 days"}]},
    {"rxId": "r2", "doctorId": "d1", "createdAt": "2026-09-01T10:00:00Z",
     "diagnosis": "viral fever",
     "medicines": [{"label": "Dolo 650mg", "frequency": "1-0-1", "duration": "5 days"}],
     "next_visit": "5 days"},
]
DOCTORS = {"d1": {"name": "Dr. Meera Krishnan"}}


def test_context_is_recent_first_and_plain_timing():
    ctx = history_chat.build_context(PATIENT, RXS, DOCTORS)
    assert [r["rxId"] for r in ctx["prescriptions"]] == ["r2", "r1"]
    timing = ctx["prescriptions"][1]["medicines"][0]["timing"]
    assert timing == "1 tablet morning, after food, for 30 days"
    assert ctx["prescriptions"][0]["doctor"] == "Dr. Meera Krishnan"
    assert ctx["patient"]["chronicConditions"] == ["Hypertension"]


def test_ask_returns_answer_and_filters_unknown_sources():
    ctx = history_chat.build_context(PATIENT, RXS, DOCTORS)
    client = StubLLM({"answer": "Dolo 650mg, 1 tablet morning and 1 at night for 5 days.",
                      "sources": ["r2", "made-up"]})
    out = history_chat.ask("raat ko kya lena hai?", ctx, client=client)
    assert out["answer"].startswith("Dolo 650mg") and out["sources"] == ["r2"]


def test_ask_without_records_or_with_llm_failure_is_safe():
    from core.llm import LLMAPIError

    assert history_chat.ask("q", {"prescriptions": []})["sources"] == []
    ctx = history_chat.build_context(PATIENT, RXS, DOCTORS)
    out = history_chat.ask("q", ctx, client=StubLLM(error=LLMAPIError("down")))
    assert "try again" in out["answer"] and out["error"] == "down"
    bad = history_chat.ask("q", ctx, client=StubLLM({"nope": 1}))
    assert bad["sources"] == [] and "could not" in bad["answer"]
