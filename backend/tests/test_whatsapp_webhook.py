"""Meta webhook: API Gateway v2 event -> handlers.whatsapp_webhook -> core.inbound -> moto."""
import base64
import json
import logging
import os

import pytest
from moto import mock_aws

from core import inbound, messaging
from core import store as store_mod
from core.messaging import compute_signature
from core.store import Store, create_tables
from scripts import seed_demo_data

SECRET = "test-app-secret"
VERIFY = "my-verify-token"
DOCTOR = seed_demo_data.DOCTOR_ID
NEW_PHONE = "+919876543210"
KNOWN_PHONE = "+919800000001"  # pat-demo-001 from the demo seed


@pytest.fixture
def sent(monkeypatch):
    calls = []
    monkeypatch.setattr(messaging, "DRY_RUN", True)
    original = messaging.send_text

    def capture(to, body, **kw):
        out = original(to, body, **kw)
        calls.append(out)
        return out

    monkeypatch.setattr(messaging, "send_text", capture)
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
        from handlers import whatsapp_webhook as mod

        monkeypatch.setattr(mod, "_store", st)
        monkeypatch.setattr(mod, "WA_VERIFY_TOKEN", VERIFY)
        monkeypatch.setattr(messaging, "WA_APP_SECRET", SECRET)
        yield mod


# -- event builders -------------------------------------------------------------

def envelope(*messages, contacts=None, statuses=None):
    value = {"messaging_product": "whatsapp",
             "metadata": {"display_phone_number": "15550001111", "phone_number_id": "111"}}
    if contacts is not None:
        value["contacts"] = contacts
    if messages:
        value["messages"] = list(messages)
    if statuses:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}]}


def text_msg(body, phone=NEW_PHONE, mid="wamid.1"):
    return {"from": messaging.wa_id(phone), "id": mid, "timestamp": "1700000000",
            "type": "text", "text": {"body": body}}


def contact(phone=NEW_PHONE, name="Asha"):
    return {"wa_id": messaging.wa_id(phone), "profile": {"name": name}}


def post_event(payload: dict, signature="valid", b64=False):
    raw = json.dumps(payload).encode()
    if signature == "valid":
        signature = compute_signature(SECRET, raw)
    headers = {"content-type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return {
        "rawPath": "/dev/webhooks/whatsapp",
        "requestContext": {"http": {"method": "POST", "path": "/dev/webhooks/whatsapp"}},
        "headers": headers,
        "body": base64.b64encode(raw).decode() if b64 else raw.decode(),
        "isBase64Encoded": b64,
    }


def get_event(query: dict):
    return {"rawPath": "/dev/webhooks/whatsapp",
            "requestContext": {"http": {"method": "GET", "path": "/dev/webhooks/whatsapp"}},
            "headers": {}, "queryStringParameters": query}


def start_payload(phone=NEW_PHONE, name="Asha", doctor=DOCTOR, mid="wamid.1"):
    return envelope(text_msg(f"START {doctor}", phone, mid), contacts=[contact(phone, name)])


# -- verification handshake -----------------------------------------------------

def test_verification_echoes_challenge_for_correct_token(webhook):
    res = webhook.handler(get_event({"hub.mode": "subscribe", "hub.verify_token": VERIFY,
                                     "hub.challenge": "1158201444"}), None)
    assert res["statusCode"] == 200
    assert res["body"] == "1158201444"
    assert res["headers"]["Content-Type"] == "text/plain"


def test_verification_rejects_wrong_token(webhook):
    res = webhook.handler(get_event({"hub.mode": "subscribe", "hub.verify_token": "nope",
                                     "hub.challenge": "1"}), None)
    assert res["statusCode"] == 403


# -- signature gate -------------------------------------------------------------

def test_bad_signature_is_403_and_nothing_is_processed(webhook, sent):
    res = webhook.handler(post_event(start_payload(), signature="sha256=deadbeef"), None)
    assert res["statusCode"] == 403
    assert webhook.store().get_patient_by_phone(NEW_PHONE) is None
    assert sent == []


def test_missing_signature_is_403(webhook):
    assert webhook.handler(post_event(start_payload(), signature=None), None)["statusCode"] == 403


def test_valid_post_returns_200(webhook):
    res = webhook.handler(post_event(envelope(text_msg("hello"))), None)
    assert res["statusCode"] == 200 and res["body"] == "OK"


def test_base64_encoded_body_is_verified_the_same(webhook):
    res = webhook.handler(post_event(start_payload(), b64=True), None)
    assert res["statusCode"] == 200
    assert webhook.store().get_patient_by_phone(NEW_PHONE)["doctorId"] == DOCTOR


def test_status_only_payload_is_acknowledged_without_processing(webhook, sent):
    payload = envelope(statuses=[{"id": "wamid.out", "status": "delivered",
                                  "recipient_id": messaging.wa_id(KNOWN_PHONE)}])
    res = webhook.handler(post_event(payload), None)
    assert res["statusCode"] == 200
    assert sent == []


# -- async dispatch + dedupe ------------------------------------------------------

def test_on_lambda_it_reinvokes_itself_and_returns_before_processing(webhook, monkeypatch, sent):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "medscribe-whatsapp-webhook-dev")
    invoked = []

    class FakeLambda:
        def invoke(self, **kw):
            invoked.append(kw)

    monkeypatch.setattr(webhook.boto3, "client", lambda name, **kw: FakeLambda())
    res = webhook.handler(post_event(start_payload()), None)
    assert res["statusCode"] == 200
    assert invoked[0]["InvocationType"] == "Event"
    assert invoked[0]["FunctionName"] == "medscribe-whatsapp-webhook-dev"
    assert webhook.store().get_patient_by_phone(NEW_PHONE) is None  # deferred

    webhook.handler(json.loads(invoked[0]["Payload"]), None)  # the async invocation
    assert webhook.store().get_patient_by_phone(NEW_PHONE)["doctorId"] == DOCTOR


def test_redelivered_message_id_is_processed_once(webhook, sent):
    webhook.handler(post_event(start_payload(mid="wamid.same")), None)
    webhook.handler(post_event(start_payload(mid="wamid.same")), None)
    assert len(sent) == 1


# -- START --------------------------------------------------------------------

def test_start_links_new_patient_and_records_consent(webhook, sent):
    webhook.handler(post_event(start_payload()), None)
    p = webhook.store().get_patient_by_phone(NEW_PHONE)
    assert p["doctorId"] == DOCTOR
    assert p["name"] == "Asha"
    assert set(p["consent"]["scopes"]) >= {"recording", "storage", "whatsapp_delivery"}
    assert p["consent"]["at"] and p["waLinkedAt"] and p["lastInboundAt"]
    assert len(sent) == 1 and sent[0]["dry_run"] is True
    assert "Dr. Meera Krishnan" in sent[0]["summary"]
    assert sent[0]["to"] == NEW_PHONE


def test_start_shows_in_doctor_waiting_list(webhook):
    before = {p["patientId"] for p in webhook.store().list_patients_for_doctor(DOCTOR)}
    webhook.handler(post_event(start_payload()), None)
    after = {p["patientId"] for p in webhook.store().list_patients_for_doctor(DOCTOR)}
    assert len(after) == len(before) + 1


def test_start_for_existing_patient_keeps_identity_and_relinks(webhook, sent):
    before = webhook.store().get_patient_by_phone(KNOWN_PHONE)
    webhook.handler(post_event(start_payload(phone=KNOWN_PHONE, name="Someone Else")), None)
    after = webhook.store().get_patient_by_phone(KNOWN_PHONE)
    assert after["patientId"] == before["patientId"]
    assert after["name"] == before["name"]
    assert after["consent"]["via"] == "whatsapp_start"


def test_start_with_unknown_doctor_does_not_create_patient(webhook, sent):
    webhook.handler(post_event(start_payload(doctor="doc-nobody")), None)
    assert webhook.store().get_patient_by_phone(NEW_PHONE) is None
    assert len(sent) == 1 and "not recognised" in sent[0]["summary"]


def test_start_without_contact_block_falls_back_to_phone_as_name(webhook):
    webhook.handler(post_event(envelope(text_msg(f"START {DOCTOR}"))), None)
    assert webhook.store().get_patient_by_phone(NEW_PHONE)["name"] == NEW_PHONE


# -- TAKEN --------------------------------------------------------------------

def _pending_reminder(st, patient_id, due, sent_at):
    return st.put_reminder(patient_id, {"dueAt": due, "brand_id": "B001", "label": "Dolo 650mg",
                                        "status": "SENT", "sentAt": sent_at})


@pytest.mark.parametrize("body", ["TAKEN", "taken", "1", " Taken "])
def test_taken_marks_most_recent_sent_reminder(webhook, sent, body):
    st = webhook.store()
    pid = st.get_patient_by_phone(KNOWN_PHONE)["patientId"]
    older = _pending_reminder(st, pid, "2026-09-19T09:00:00Z", "2026-09-19T09:00:05Z")
    newer = _pending_reminder(st, pid, "2026-09-19T21:00:00Z", "2026-09-19T21:00:05Z")
    st.put_reminder(pid, {"dueAt": "2026-09-20T09:00:00Z", "brand_id": "B001"})  # SCHEDULED

    webhook.handler(post_event(envelope(text_msg(body, KNOWN_PHONE))), None)

    by_id = {r["remId"]: r for r in st.list_reminders(pid)}
    assert by_id[newer["remId"]]["status"] == "TAKEN"
    assert by_id[newer["remId"]]["takenAt"]
    assert by_id[older["remId"]]["status"] == "SENT"
    assert len(sent) == 1 and "Dolo 650mg" in sent[0]["summary"]


def test_taken_via_template_quick_reply_button(webhook, sent):
    st = webhook.store()
    pid = st.get_patient_by_phone(KNOWN_PHONE)["patientId"]
    r = _pending_reminder(st, pid, "2026-09-19T09:00:00Z", "2026-09-19T09:00:05Z")
    msg = {"from": messaging.wa_id(KNOWN_PHONE), "id": "wamid.btn", "timestamp": "1",
           "type": "button", "button": {"payload": "TAKEN", "text": "Taken ✅"}}
    webhook.handler(post_event(envelope(msg)), None)
    got = st.list_reminders(pid)[0]
    assert got["remId"] == r["remId"] and got["status"] == "TAKEN"


def test_taken_via_interactive_reply(webhook, sent):
    st = webhook.store()
    pid = st.get_patient_by_phone(KNOWN_PHONE)["patientId"]
    _pending_reminder(st, pid, "2026-09-19T09:00:00Z", "2026-09-19T09:00:05Z")
    msg = {"from": messaging.wa_id(KNOWN_PHONE), "id": "wamid.int", "timestamp": "1",
           "type": "interactive",
           "interactive": {"type": "button_reply",
                           "button_reply": {"id": "TAKEN", "title": "Taken"}}}
    webhook.handler(post_event(envelope(msg)), None)
    assert st.list_reminders(pid)[0]["status"] == "TAKEN"


def test_taken_with_nothing_pending_is_acknowledged_without_error(webhook, sent):
    webhook.handler(post_event(envelope(text_msg("1", KNOWN_PHONE))), None)
    assert len(sent) == 1 and "No reminder" in sent[0]["summary"]


def test_taken_from_unknown_number_is_ignored(webhook, sent):
    res = webhook.handler(post_event(envelope(text_msg("TAKEN"))), None)
    assert res["statusCode"] == 200
    assert sent == []


# -- everything else -> history chat stub ---------------------------------------

def test_other_text_is_logged_not_replied(webhook, sent, caplog):
    with caplog.at_level(logging.INFO, logger="core.inbound"):
        payload = envelope(text_msg("mujhe kal ki dawai batao", KNOWN_PHONE))
        webhook.handler(post_event(payload), None)
    assert sent == []
    assert any("history chat" in r.message for r in caplog.records)
    assert webhook.store().get_patient_by_phone(KNOWN_PHONE)["lastInboundAt"]


def test_voice_note_is_routed_to_chat_stub_not_dropped(webhook, sent, caplog):
    msg = {"from": messaging.wa_id(KNOWN_PHONE), "id": "wamid.audio", "timestamp": "1",
           "type": "audio", "audio": {"id": "MEDIA1", "mime_type": "audio/ogg", "voice": True}}
    with caplog.at_level(logging.INFO, logger="core.inbound"):
        webhook.handler(post_event(envelope(msg)), None)
    assert any("[audio]" in r.message for r in caplog.records)
    assert sent == []


def test_multiple_messages_in_one_delivery_are_all_processed(webhook, sent):
    payload = envelope(text_msg(f"START {DOCTOR}", NEW_PHONE, "wamid.a"),
                       text_msg("1", KNOWN_PHONE, "wamid.b"),
                       contacts=[contact(NEW_PHONE)])
    webhook.handler(post_event(payload), None)
    assert webhook.store().get_patient_by_phone(NEW_PHONE)["doctorId"] == DOCTOR
    assert len(sent) == 2


def test_neutral_message_shape():
    m = webhook_module().neutral_message(text_msg("hi", "+911234567890", "wamid.z"), "Ravi")
    assert m == {"From": "+911234567890", "Body": "hi", "ProfileName": "Ravi",
                 "ButtonPayload": None, "MessageSid": "wamid.z", "Type": "text",
                 "Timestamp": "1700000000"}


def webhook_module():
    os.environ.setdefault("SSM_PREFIX", "")
    from handlers import whatsapp_webhook

    return whatsapp_webhook


def test_classify():
    assert inbound.classify(f"START {DOCTOR}") == ("start", DOCTOR)
    assert inbound.classify("TAKEN") == ("taken", None)
    assert inbound.classify("1") == ("taken", None)
    assert inbound.classify("kya haal hai") == ("chat", None)
    assert inbound.classify("", button_payload="TAKEN") == ("taken", None)
