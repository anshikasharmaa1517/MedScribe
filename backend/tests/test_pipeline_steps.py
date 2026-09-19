"""Post-approval pipeline steps, run in spec order against moto (DynamoDB + S3).

The LLM is stubbed, WeasyPrint is absent locally (so RenderPDF takes the HTML
fallback), and messaging is in DRY_RUN - exactly the shape of a first deploy.
"""
import os
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from core import store as store_mod
from core.store import Store, create_tables
from scripts import seed_demo_data
from tests.test_pipeline import SAMPLE, StubLLM, extraction, med

BUCKET = "medscribe-test"


@pytest.fixture
def env(monkeypatch):
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("SSM_PREFIX", "")
    with mock_aws():
        db = store_mod.dynamodb_resource(endpoint_url=None, region="ap-south-1")
        create_tables(db)
        st = Store(db)
        seed_demo_data.seed(st)
        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(Bucket=BUCKET,
                         CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        from handlers import pipeline_steps as steps

        monkeypatch.setattr(steps, "_store", st)
        monkeypatch.setattr(steps, "_client", StubLLM(SAMPLE))
        monkeypatch.setattr(steps, "MEDSCRIBE_BUCKET", BUCKET)
        yield steps, st, s3


def start_consult(st, llm_payload=SAMPLE):
    from core.pipeline import process

    c = st.create_consult("pat-demo-001", seed_demo_data.DOCTOR_ID, {
        "status": "LIVE",
        "transcript": [{"seq": 1, "speaker": "doctor", "text": "Dolo 650 le lena", "at": "x"}],
        "draft": process("t", None, client=StubLLM(llm_payload)),
    })
    return {"consultId": c["consultId"], "patientId": c["patientId"], "doctorId": c["doctorId"],
            "rxId": "rx-test-1", "approvedAt": "2026-09-19T12:00:00Z"}


def run(steps, names, inp):
    for name in names:
        inp = steps.handler({"step": name, "input": inp}, None)
    return inp


FULL = ["final_transcript", "final_extract", "resolve_validate", "render_pdf", "persist_rx",
        "send_prescription", "send_generics", "schedule_reminders"]


def test_happy_path_end_to_end(env):
    steps, st, s3 = env
    out = run(steps, FULL, start_consult(st))

    assert out["blocksApproval"] is False and out["persisted"] is True
    assert out["document"]["format"] == "html"           # no WeasyPrint locally -> HTML fallback
    assert out["document"]["key"] == "rx/rx-test-1.html"
    obj = s3.get_object(Bucket=BUCKET, Key="rx/rx-test-1.html")
    assert obj["ContentType"].startswith("text/html")
    assert "transcriptText" not in out

    c = st.get_consult_by_id(out["consultId"])
    assert c["status"] == "APPROVED" and c["rxId"] == "rx-test-1"
    rx = st.get_prescription("pat-demo-001", out["approvedAt"], "rx-test-1")
    assert [m["label"] for m in rx["medicines"]] == ["Dolo 650mg", "Cetzine 10mg"]
    assert rx["audit"]["doctorEdited"] == [] and rx["sentAt"] and rx["genericsSentAt"]

    sent = out["prescriptionSend"]["results"]
    assert len(sent) == 2 and all(r["dry_run"] for r in sent)
    assert sent[0]["payload"]["type"] == "document" and sent[1]["payload"]["type"] == "text"
    assert "1 tablet morning, 1 tablet night" in sent[1]["payload"]["text"]["body"]
    assert "Crocin" in out["genericsSend"]["result"]["payload"]["text"]["body"]
    assert out["reminders"]["count"] > 0
    assert st.list_reminders("pat-demo-001")


def test_gate_returns_consult_to_dashboard(env):
    steps, st, _ = env
    junk = extraction([med("dolo 650"), med("zorblaxitron 900")], "viral fever")
    steps._client = StubLLM(junk)
    first_three = ["final_transcript", "final_extract", "resolve_validate"]
    out = run(steps, first_three, start_consult(st, junk))
    assert out["blocksApproval"] is True
    out = steps.handler({"step": "unblock", "input": out}, None)
    c = st.get_consult_by_id(out["consultId"])
    assert c["status"] == "LIVE" and c["approvalBlockedBy"] == ["zorblaxitron 900"]
    assert st.list_prescriptions("pat-demo-001", limit=1)[0]["rxId"] != "rx-test-1"


def test_final_extract_keeps_live_draft_on_llm_failure(env):
    from core.llm import LLMAPIError

    steps, st, _ = env
    steps._client = StubLLM(error=LLMAPIError("down"))
    out = run(steps, ["final_transcript", "final_extract"], start_consult(st))
    assert [m["matched"] for m in out["draft"]["medicines"]] == ["Dolo 650mg", "Cetzine 10mg"]
    assert "extraction_error" not in out["draft"]


def test_locked_doctor_edits_survive_final_pass_and_land_in_audit(env):
    from core.draft_edit import apply_patch

    steps, st, _ = env
    inp = start_consult(st)
    c = st.get_consult_by_id(inp["consultId"])
    draft = apply_patch(c["draft"], {"medicine": {"med_key": "B001", "frequency": "1-1-1"}})
    draft = apply_patch(draft, {"medicine": {"med_key": "B025", "deleted": True}})
    st.update_consultation(c, draft=draft)

    out = run(steps, FULL[:5], inp)
    rx = st.get_prescription("pat-demo-001", inp["approvedAt"], inp["rxId"])
    assert [m["label"] for m in rx["medicines"]] == ["Dolo 650mg"]
    assert rx["medicines"][0]["frequency"] == "1-1-1"
    assert rx["audit"]["doctorEdited"] == ["B001"] and rx["audit"]["doctorDeleted"] == ["B025"]


def test_sends_and_reminders_are_idempotent_on_retry(env):
    steps, st, _ = env
    out = run(steps, FULL, start_consult(st))
    again = run(steps, ["persist_rx", "send_prescription", "send_generics", "schedule_reminders"], out)
    assert again["prescriptionSend"]["skipped"] == "already sent"
    assert again["genericsSend"]["skipped"] == "already sent"
    assert again["reminders"]["skipped"] == "already scheduled"
    assert len(st.list_prescriptions("pat-demo-001")) == 4          # 3 seeded + 1
    assert len(st.list_reminders("pat-demo-001")) == out["reminders"]["count"]


def test_rerun_after_approval_short_circuits(env):
    steps, st, _ = env
    out = run(steps, FULL, start_consult(st))
    out2 = steps.handler({"step": "final_transcript", "input": {**out, "rxId": "rx-other"}}, None)
    assert out2["alreadyApproved"] is True and out2["rxId"] == "rx-test-1"


def test_mark_failed_records_error(env):
    steps, st, _ = env
    inp = start_consult(st)
    out = steps.handler({"step": "mark_failed", "input": {**inp, "error": {"Error": "Boom"}}}, None)
    assert out["markedFailed"]
    assert st.get_consult_by_id(inp["consultId"])["status"] == "APPROVAL_FAILED"


def test_reminder_plan_slots_and_ids():
    from handlers.pipeline_steps import reminder_plan

    ist = timezone(timedelta(hours=5, minutes=30))
    start = datetime(2026, 9, 19, 8, 0, tzinfo=ist)
    rx = {"rxId": "r1", "medicines": [
        {"brand_id": "B001", "label": "Dolo 650mg", "frequency": "1-0-1", "food_relation": "after food",
         "duration": "2 days"},
        {"brand_id": "B085", "label": "Pan 40mg", "frequency": "OD", "food_relation": "before food",
         "duration": "1 week"},
        {"brand_id": "B999", "label": "Odd", "frequency": None},
    ]}
    plan = reminder_plan(rx, start)
    dolo = [p for p in plan if p["brand_id"] == "B001"]
    pan = [p for p in plan if p["brand_id"] == "B085"]
    assert len(dolo) == 4 and len(pan) == 7 and not [p for p in plan if p["brand_id"] == "B999"]
    assert dolo[0]["remId"] == "rem-r1-B001-20260919-0930"          # 09:00 + 30 min after food
    assert dolo[0]["dueAt"] == "2026-09-19T04:00:00Z"                # 09:30 IST
    assert pan[0]["remId"] == "rem-r1-B085-20260919-0830"            # 09:00 - 30 min before food
    assert len({p["remId"] for p in plan}) == len(plan)
    assert "Reply TAKEN" in dolo[0]["text"]


def test_unknown_step_raises(env):
    steps, _, _ = env
    with pytest.raises(steps.PipelineError):
        steps.handler({"step": "nope", "input": {}}, None)
