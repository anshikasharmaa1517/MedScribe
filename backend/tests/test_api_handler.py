"""End-to-end handler tests: API Gateway v2 events -> handlers.api -> moto DynamoDB."""
import json
import os

import pytest
from moto import mock_aws

from core import store as store_mod
from core.store import Store, create_tables
from scripts import seed_demo_data
from tests.test_pipeline import SAMPLE, StubLLM, extraction, med

DOCTOR = seed_demo_data.DOCTOR_ID


@pytest.fixture
def api(monkeypatch):
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("SSM_PREFIX", "")
    with mock_aws():
        db = store_mod.dynamodb_resource(endpoint_url=None, region="ap-south-1")
        create_tables(db)
        st = Store(db)
        seed_demo_data.seed(st)
        from handlers import api as mod

        monkeypatch.setattr(mod, "_store", st)
        monkeypatch.setattr(mod, "_client", StubLLM(SAMPLE))
        monkeypatch.setattr(mod, "EXTRACTION_INTERVAL_SECONDS", 0)
        yield mod


def event(method, path, body=None, doctor=DOCTOR, query=None, jwt=True):
    ev = {
        "rawPath": path,
        "requestContext": {"http": {"method": method, "path": path}, "stage": "dev"},
        "headers": {},
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
    }
    if jwt and doctor:
        claims = {"sub": "cognito-sub", "custom:doctorId": doctor}
        ev["requestContext"]["authorizer"] = {"jwt": {"claims": claims}}
    return ev


def call(api, method, path, body=None, **kw):
    res = api.handler(event(method, path, body, **kw), None)
    return res["statusCode"], json.loads(res["body"])


def start(api, patient="pat-demo-001"):
    status, c = call(api, "POST", "/consults", {"patientId": patient})
    assert status == 201
    return c["consultId"]


def test_unauthenticated_is_401(api):
    status, body = call(api, "GET", "/patients", jwt=False)
    assert status == 401 and "error" in body


def test_list_patients_scoped_to_doctor(api):
    status, patients = call(api, "GET", "/patients")
    assert status == 200 and len(patients) == 3
    assert {p["patientId"] for p in patients} == {"pat-demo-001", "pat-demo-002", "pat-demo-003"}
    assert all(p["lastVisit"] for p in patients)
    assert call(api, "GET", "/patients", doctor="someone-else")[1] == []


def test_create_consult_requires_own_patient(api):
    assert call(api, "POST", "/consults", {"patientId": "nope"})[0] == 404
    assert call(api, "POST", "/consults", {"patientId": "pat-demo-001"}, doctor="other")[0] == 404
    cid = start(api)
    status, c = call(api, "GET", f"/consults/{cid}")
    assert status == 200 and c["status"] == "LIVE" and c["draft"]["medicines"] == []
    assert call(api, "GET", f"/consults/{cid}", doctor="other")[0] == 404


def test_transcript_then_draft_runs_extraction(api):
    cid = start(api)
    status, out = call(api, "POST", f"/consults/{cid}/transcript",
                       {"text": "Dolo 650 le lena", "speaker": "doctor", "seq": 1})
    assert status == 200 and out == {"accepted": True, "seq": 1}
    assert call(api, "POST", f"/consults/{cid}/transcript", {"text": ""})[0] == 400

    status, draft = call(api, "GET", f"/consults/{cid}/draft")
    assert status == 200
    assert [m["matched"] for m in draft["medicines"]] == ["Dolo 650mg", "Cetzine 10mg"]
    assert draft["conditions_matched"] == ["Viral fever"] and draft["updatedAt"]

    status, c = call(api, "GET", f"/consults/{cid}")
    assert c["transcript"][0]["text"] == "Dolo 650 le lena"


def test_draft_not_reextracted_until_interval(api, monkeypatch):
    cid = start(api)
    call(api, "POST", f"/consults/{cid}/transcript", {"text": "x", "speaker": "doctor", "seq": 1})
    call(api, "GET", f"/consults/{cid}/draft")
    monkeypatch.setattr(api, "EXTRACTION_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(api, "_client", StubLLM(extraction([med("crocin 650")], "viral fever")))
    call(api, "POST", f"/consults/{cid}/transcript", {"text": "y", "speaker": "doctor", "seq": 2})
    _, draft = call(api, "GET", f"/consults/{cid}/draft")
    assert [m["matched"] for m in draft["medicines"]] == ["Dolo 650mg", "Cetzine 10mg"]


def test_patch_locks_and_survives_next_pass(api):
    cid = start(api)
    call(api, "POST", f"/consults/{cid}/transcript", {"text": "x", "speaker": "doctor", "seq": 1})
    call(api, "GET", f"/consults/{cid}/draft")

    status, draft = call(api, "PATCH", f"/consults/{cid}/draft",
                         {"medicine": {"med_key": "B001", "frequency": "1-1-1"}})
    assert status == 200
    dolo = next(m for m in draft["medicines"] if m["med_key"] == "B001")
    assert dolo["frequency"] == "1-1-1" and dolo["locked"] is True

    call(api, "POST", f"/consults/{cid}/transcript", {"text": "y", "speaker": "doctor", "seq": 2})
    _, draft = call(api, "GET", f"/consults/{cid}/draft")
    assert next(m for m in draft["medicines"] if m["med_key"] == "B001")["frequency"] == "1-1-1"

    bad = {"medicine": {"med_key": "nope", "deleted": True}}
    assert call(api, "PATCH", f"/consults/{cid}/draft", bad)[0] == 400
    dx = {"field": "diagnosis", "value": "dengue"}
    assert call(api, "PATCH", f"/consults/{cid}/draft", dx)[1]["locked_fields"] == ["diagnosis"]


def test_approve_blocked_by_resolve_then_succeeds(api, monkeypatch):
    cid = start(api)
    ex = extraction([med("dolo 650"), med("zorblaxitron 900")], "viral fever")
    monkeypatch.setattr(api, "_client", StubLLM(ex))
    call(api, "POST", f"/consults/{cid}/transcript", {"text": "x", "speaker": "doctor", "seq": 1})
    _, draft = call(api, "GET", f"/consults/{cid}/draft")
    assert draft["blocks_approval"]
    junk = draft["approval_blocked_by"][0]

    status, body = call(api, "POST", f"/consults/{cid}/approve")
    assert status == 409 and "block" in body["error"]

    fix = {"medicine": {"med_key": junk, "brand_id": "B016"}}
    call(api, "PATCH", f"/consults/{cid}/draft", fix)
    status, body = call(api, "POST", f"/consults/{cid}/approve")
    assert status == 200 and body["status"] == "APPROVED" and body["rxId"]

    rx = api.store().list_prescriptions("pat-demo-001", limit=1)[0]
    assert rx["rxId"] == body["rxId"] and rx["consultId"] == cid
    assert [m["label"] for m in rx["medicines"]] == ["Dolo 650mg", "Zerodol 100mg"]
    late = {"text": "late", "seq": 9}
    assert call(api, "POST", f"/consults/{cid}/transcript", late)[0] == 409
    assert call(api, "POST", f"/consults/{cid}/approve")[1]["rxId"] == body["rxId"]


def test_brand_search(api):
    status, hits = call(api, "GET", "/brands", query={"q": "dolo"})
    assert status == 200 and {h["brand_id"] for h in hits} >= {"B001", "B002"}
    assert call(api, "GET", "/brands", query={"q": ""})[1] == []


def test_unknown_route_and_bad_json(api):
    assert call(api, "GET", "/nope")[0] == 404
    res = api.handler({**event("POST", "/consults"), "body": "{not json"}, None)
    assert res["statusCode"] == 400


def test_llm_failure_keeps_prior_draft(api, monkeypatch):
    from core.llm import LLMAPIError

    cid = start(api)
    call(api, "POST", f"/consults/{cid}/transcript", {"text": "x", "speaker": "doctor", "seq": 1})
    _, good = call(api, "GET", f"/consults/{cid}/draft")
    monkeypatch.setattr(api, "_client", StubLLM(error=LLMAPIError("down")))
    call(api, "POST", f"/consults/{cid}/transcript", {"text": "y", "speaker": "doctor", "seq": 2})
    status, draft = call(api, "GET", f"/consults/{cid}/draft")
    assert status == 200 and draft["extraction_error"] == "down"
    assert [m["med_key"] for m in draft["medicines"]] == [m["med_key"] for m in good["medicines"]]
