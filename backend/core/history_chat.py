"""'Ask my history': one LLM call over a single patient's records.

The caller resolves the patient from the Cognito identity before this runs, so
the model only ever sees one patient's data. The prompt confines it to those
records: it may summarise and locate, it may not diagnose, change a dose or
suggest a medicine - that is the doctor's job, and rule 7 applies to advice too.
"""
import json

from core.llm import LLMError, get_client
from core.messaging import plain_timing

MAX_RECORDS = 12
MAX_TOKENS = 600

SYSTEM_PROMPT = """You answer a patient's questions about their own medical records from one
clinic. You are given the records as JSON and a question, often in Hindi/English/Hinglish.
Answer in the language of the question, plainly and briefly (2-5 sentences).

Rules:
1. Use ONLY the records given. If the answer is not in them, say so - never guess.
2. Never diagnose, never suggest a new medicine, dose or stopping a medicine, and never
   say whether a symptom is serious. For anything like that, tell them to contact the
   doctor named in the records.
3. When you mention a medicine, use the brand label from the records and spell the timing
   out plainly (e.g. "1 tablet morning and 1 at night, after food").
4. Return ONLY JSON: {"answer": "string", "sources": ["rxId", ...]} where sources are the
   rxIds you relied on (empty list if none)."""


def _record(rx: dict, doctor_name: str | None) -> dict:
    return {
        "rxId": rx.get("rxId"),
        "date": (rx.get("approvedAt") or rx.get("createdAt") or "")[:10],
        "doctor": doctor_name,
        "diagnosis": rx.get("diagnosis"),
        "symptoms": rx.get("symptoms") or [],
        "tests_advised": rx.get("tests_advised") or [],
        "medicines": [
            {"label": m.get("label"), "timing": plain_timing(m), "duration": m.get("duration")}
            for m in rx.get("medicines") or []
        ],
        "next_visit": rx.get("next_visit"),
    }


def build_context(patient: dict, prescriptions: list[dict], doctors: dict[str, dict]) -> dict:
    recent = sorted(prescriptions, key=lambda r: r.get("createdAt") or "", reverse=True)
    recent = recent[:MAX_RECORDS]
    return {
        "patient": {"name": patient.get("name"), "age": patient.get("age"),
                    "sex": patient.get("sex"),
                    "chronicConditions": patient.get("chronicConditions") or [],
                    "allergies": patient.get("allergies") or []},
        "prescriptions": [_record(rx, (doctors.get(rx.get("doctorId")) or {}).get("name"))
                          for rx in recent],
    }


def ask(question: str, context: dict, client=None) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("question is required")
    if not context.get("prescriptions"):
        return {"answer": "There are no prescriptions on record yet.", "sources": []}
    client = client or get_client()
    user = "Records:\n" + json.dumps(context, ensure_ascii=False) + f"\n\nQuestion: {question}"
    try:
        out = client.converse_json(SYSTEM_PROMPT, user, max_tokens=MAX_TOKENS)
    except LLMError as e:
        return {"answer": "Sorry, I could not read your records just now. Please try again.",
                "sources": [], "error": str(e)}
    answer = out.get("answer") if isinstance(out, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        return {"answer": "Sorry, I could not answer that from your records.", "sources": []}
    known = {rx["rxId"] for rx in context["prescriptions"]}
    sources = [s for s in (out.get("sources") or []) if isinstance(s, str) and s in known]
    return {"answer": answer.strip(), "sources": sources}
