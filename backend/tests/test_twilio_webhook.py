"""Inbound WhatsApp: API Gateway v2 event -> handlers.twilio_webhook -> core.inbound -> moto."""
import base64
import os
import urllib.parse

import pytest
from moto import mock_aws

from core import inbound, messaging
from core import store as store_mod
from core.messaging import compute_signature
from core.store import Store, create_tables
from scripts import seed_demo_data

TOKEN = "test-auth-token"
HOST = "abc123.execute-api.ap-south-1.amazonaws.com"
PATH = "/dev/webhooks/twilio"
URL = f"https://{HOST}{PATH}"
DOCTOR = seed_demo_data.DOCTOR_ID
NEW_PHONE = "+919876543210"
KNOWN_PHONE = "+919800000001"  # pat-demo-001 from the demo seed


@pytest.fixture
def sent(monkeypatch):
    """Capture outbound replies instead of sending; asserts dry-run is what the default does."""
    calls = []
    monkeypatch.setattr(messaging, "MESSAGING_DRY_RUN", True)
    original = messaging.send_whatsapp

    def capture(to, body, **kw):
        out = original(to, body, **kw)
        calls.append(out)
        return out

    monkeypatch.setattr(messaging, "send_whatsapp", capture)
    return calls


@pytest.fixture
def webhook(monkeypatch, sent):
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("SSM_PREFIX", "")
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    with mock_aws():
        db = store_mod.dynamodb_resource(endpoint_url=None, region="ap-south-1")
        create_tables(db)
        st = Store(db)
        seed_demo_data.seed(st)
        from handlers import twilio_webhook as mod

        monkeypatch.setattr(mod, "_store", st)
        monkeypatch.setattr(messaging, "TWILIO_AUTH_TOKEN", TOKEN)
        yield mod


def twilio_event(params: dict, signature: str | None = "valid", b64=False):
    body = urllib.parse.urlencode(params)
    if signature == "valid":
        signature = compute_signature(TOKEN, URL, params)
    headers = {"host": HOST, "content-type": "application/x-www-form-urlencoded"}
    if signature is not None:
        headers["X-Twilio-Signature"] = signature
    return {
        "rawPath": PATH,
        "rawQueryString": "",
        "requestContext": {"http": {"method": "POST", "path": PATH}, "stage": "dev"},
        "headers": headers,
        "body": base64.b64encode(body.encode()).decode() if b64 else body,
        "isBase64Encoded": b64,
    }


def msg(body, phone=NEW_PHONE, **extra):
    return {"From": f"whatsapp:{phone}", "Body": body, "MessageSid": "SM1",
            "ProfileName": "Asha", **extra}


# -- signature gate -------------------------------------------------------------

def test_bad_signature_is_403_and_nothing_is_processed(webhook, sent):
    res = webhook.handler(twilio_event(msg(f"START {DOCTOR}"), signature="nope"), None)
    assert res["statusCode"] == 403
    assert webhook.store().get_patient_by_phone(NEW_PHONE) is None
    assert sent == []


def test_missing_signature_is_403(webhook):
    res = webhook.handler(twilio_event(msg("TAKEN"), signature=None), None)
    assert res["statusCode"] == 403


def test_valid_signature_returns_200_empty_twiml(webhook):
    res = webhook.handler(twilio_event(msg("hello")), None)
    assert res["statusCode"] == 200
    assert res["headers"]["Content-Type"] == "text/xml"
    assert "<Response></Response>" in res["body"]


def test_base64_encoded_body_is_verified_the_same(webhook):
    res = webhook.handler(twilio_event(msg(f"START {DOCTOR}"), b64=True), None)
    assert res["statusCode"] == 200
    assert webhook.store().get_patient_by_phone(NEW_PHONE)["doctorId"] == DOCTOR


# -- async dispatch -------------------------------------------------------------

def test_on_lambda_it_reinvokes_itself_and_returns_before_processing(webhook, monkeypatch, sent):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "medscribe-twilio-webhook-dev")
    invoked = []

    class FakeLambda:
        def invoke(self, **kw):
            invoked.append(kw)

    monkeypatch.setattr(webhook.boto3, "client", lambda name, **kw: FakeLambda())
    res = webhook.handler(twilio_event(msg(f"START {DOCTOR}")), None)
    assert res["statusCode"] == 200
    assert invoked[0]["InvocationType"] == "Event"
    assert invoked[0]["FunctionName"] == "medscribe-twilio-webhook-dev"
    assert webhook.store().get_patient_by_phone(NEW_PHONE) is None  # deferred

    import json

    payload = json.loads(invoked[0]["Payload"])
    webhook.handler(payload, None)  # the async invocation
    assert webhook.store().get_patient_by_phone(NEW_PHONE)["doctorId"] == DOCTOR


# -- START --------------------------------------------------------------------

def test_start_links_new_patient_and_records_consent(webhook, sent):
    webhook.handler(twilio_event(msg(f"join happy-tiger START {DOCTOR}")), None)
    p = webhook.store().get_patient_by_phone(NEW_PHONE)
    assert p["doctorId"] == DOCTOR
    assert p["name"] == "Asha"
    assert set(p["consent"]["scopes"]) >= {"recording", "storage", "whatsapp_delivery"}
    assert p["consent"]["at"] and p["waLinkedAt"] and p["lastInboundAt"]
    assert len(sent) == 1 and sent[0]["dry_run"] is True
    assert "Dr. Meera Krishnan" in sent[0]["body"]
    assert sent[0]["to"] == f"whatsapp:{NEW_PHONE}"


def test_start_shows_in_doctor_waiting_list(webhook):
    before = {p["patientId"] for p in webhook.store().list_patients_for_doctor(DOCTOR)}
    webhook.handler(twilio_event(msg(f"START {DOCTOR}")), None)
    after = {p["patientId"] for p in webhook.store().list_patients_for_doctor(DOCTOR)}
    assert len(after) == len(before) + 1


def test_start_for_existing_patient_keeps_identity_and_relinks(webhook, sent):
    before = webhook.store().get_patient_by_phone(KNOWN_PHONE)
    webhook.handler(twilio_event(msg(f"START {DOCTOR}", phone=KNOWN_PHONE)), None)
    after = webhook.store().get_patient_by_phone(KNOWN_PHONE)
    assert after["patientId"] == before["patientId"]
    assert after["name"] == before["name"]  # ProfileName does not overwrite a known name
    assert after["consent"]["via"] == "whatsapp_start"


def test_start_with_unknown_doctor_does_not_create_patient(webhook, sent):
    webhook.handler(twilio_event(msg("START doc-nobody")), None)
    assert webhook.store().get_patient_by_phone(NEW_PHONE) is None
    assert len(sent) == 1 and "not recognised" in sent[0]["body"]


def test_start_is_case_insensitive(webhook):
    webhook.handler(twilio_event(msg(f"start {DOCTOR}")), None)
    assert webhook.store().get_patient_by_phone(NEW_PHONE)["doctorId"] == DOCTOR


# -- TAKEN --------------------------------------------------------------------

def _pending_reminder(st, patient_id, due, sent_at, **extra):
    r = st.put_reminder(patient_id, {"dueAt": due, "brand_id": "B001", "label": "Dolo 650mg",
                                     "status": "SENT", "sentAt": sent_at, **extra})
    return r


@pytest.mark.parametrize("body", ["TAKEN", "taken", "1", " Taken "])
def test_taken_marks_most_recent_sent_reminder(webhook, sent, body):
    st = webhook.store()
    pid = st.get_patient_by_phone(KNOWN_PHONE)["patientId"]
    older = _pending_reminder(st, pid, "2026-09-19T09:00:00Z", "2026-09-19T09:00:05Z")
    newer = _pending_reminder(st, pid, "2026-09-19T21:00:00Z", "2026-09-19T21:00:05Z")
    st.put_reminder(pid, {"dueAt": "2026-09-20T09:00:00Z", "brand_id": "B001"})  # SCHEDULED

    webhook.handler(twilio_event(msg(body, phone=KNOWN_PHONE)), None)

    by_id = {r["remId"]: r for r in st.list_reminders(pid)}
    assert by_id[newer["remId"]]["status"] == "TAKEN"
    assert by_id[newer["remId"]]["takenAt"]
    assert by_id[older["remId"]]["status"] == "SENT"
    assert len(sent) == 1 and "Dolo 650mg" in sent[0]["body"]


def test_taken_via_button_payload(webhook, sent):
    st = webhook.store()
    pid = st.get_patient_by_phone(KNOWN_PHONE)["patientId"]
    r = _pending_reminder(st, pid, "2026-09-19T09:00:00Z", "2026-09-19T09:00:05Z")
    webhook.handler(
        twilio_event(msg("Taken ✅", phone=KNOWN_PHONE, ButtonPayload="TAKEN")), None
    )
    assert st.list_reminders(pid)[0]["remId"] == r["remId"]
    assert st.list_reminders(pid)[0]["status"] == "TAKEN"


def test_taken_with_nothing_pending_is_acknowledged_without_error(webhook, sent):
    webhook.handler(twilio_event(msg("1", phone=KNOWN_PHONE)), None)
    assert len(sent) == 1 and "No reminder" in sent[0]["body"]


def test_taken_from_unknown_number_is_ignored(webhook, sent):
    res = webhook.handler(twilio_event(msg("TAKEN")), None)
    assert res["statusCode"] == 200
    assert sent == []


# -- everything else -> history chat stub ---------------------------------------

def test_other_text_is_logged_not_replied(webhook, sent, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="core.inbound"):
        webhook.handler(twilio_event(msg("mujhe kal ki dawai batao", phone=KNOWN_PHONE)), None)
    assert sent == []
    assert any("history chat" in r.message for r in caplog.records)
    assert webhook.store().get_patient_by_phone(KNOWN_PHONE)["lastInboundAt"]


def test_classify():
    assert inbound.classify(f"join happy-tiger START {DOCTOR}") == ("start", DOCTOR)
    assert inbound.classify("TAKEN") == ("taken", None)
    assert inbound.classify("1") == ("taken", None)
    assert inbound.classify("kya haal hai") == ("chat", None)
    assert inbound.classify("", button_payload="TAKEN") == ("taken", None)
