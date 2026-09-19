import hashlib
import hmac
import json

import pytest

from core import messaging
from core.messaging import (
    MessagingAPIError,
    MessagingBudgetExceeded,
    MessagingError,
    compute_signature,
    e164,
    send_document,
    send_template,
    send_text,
    valid_signature,
    wa_id,
)

SECRET = "app-secret-123"
BODY = b'{"object":"whatsapp_business_account","entry":[]}'


def test_signature_is_sha256_hmac_of_raw_body():
    expected = "sha256=" + hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert compute_signature(SECRET, BODY) == expected
    assert valid_signature(expected, BODY, app_secret=SECRET)


def test_signature_rejects_tampering_and_missing_inputs():
    good = compute_signature(SECRET, BODY)
    assert not valid_signature(good, BODY + b" ", SECRET)
    assert not valid_signature(good, BODY, "other-secret")
    assert not valid_signature(None, BODY, SECRET)
    assert not valid_signature(good, BODY, app_secret="")


def test_number_helpers():
    assert wa_id("+919800000001") == "919800000001"
    assert wa_id("919800000001") == "919800000001"
    assert wa_id("+91 98000 00001") == "919800000001"
    assert e164("919800000001") == "+919800000001"
    assert e164("+919800000001") == "+919800000001"
    assert e164("") == ""


def test_dry_run_is_the_default_and_never_posts(monkeypatch):
    calls = []
    monkeypatch.setattr(messaging, "MESSAGING_DRY_RUN", True)
    out = send_text("+919800000001", "hi", post=lambda *a: calls.append(a))
    assert out == {"dry_run": True, "to": "+919800000001", "type": "text", "summary": "hi"}
    assert calls == []


def make_post(captured, message_id="wamid.X"):
    def post(url, payload, token):
        captured.update(url=url, payload=payload, token=token)
        return {"messaging_product": "whatsapp", "messages": [{"id": message_id}]}
    return post


def test_text_send_posts_graph_payload_with_bearer_and_counts():
    captured = {}
    out = send_text("+919800000001", "hello", dry_run=False, meter=lambda: 1,
                    phone_number_id="123456", access_token="tok", post=make_post(captured))
    assert captured["url"] == "https://graph.facebook.com/v25.0/123456/messages"
    assert captured["token"] == "tok"
    assert captured["payload"] == {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": "919800000001",
        "type": "text", "text": {"preview_url": False, "body": "hello"},
    }
    assert out == {"dry_run": False, "to": "+919800000001", "type": "text", "summary": "hello",
                   "message_id": "wamid.X", "sent_count": 1}


def test_template_send_fills_body_params_in_order():
    captured = {}
    send_template("+919800000001", "medicine_reminder", "en", ["Asha", "Dolo 650", "9 pm"],
                  dry_run=False, meter=lambda: 1, phone_number_id="1", access_token="t",
                  post=make_post(captured))
    assert captured["payload"]["type"] == "template"
    assert captured["payload"]["template"] == {
        "name": "medicine_reminder", "language": {"code": "en"},
        "components": [{"type": "body", "parameters": [
            {"type": "text", "text": "Asha"}, {"type": "text", "text": "Dolo 650"},
            {"type": "text", "text": "9 pm"},
        ]}],
    }


def test_template_without_params_has_no_components():
    captured = {}
    send_template("+91", "hello_world", "en_US", dry_run=False, meter=lambda: 1,
                  phone_number_id="1", access_token="t", post=make_post(captured))
    assert captured["payload"]["template"] == {"name": "hello_world", "language": {"code": "en_US"}}


def test_document_send_carries_link_filename_caption():
    captured = {}
    send_document("+91", "https://s3/rx.pdf?sig=1", "rx.pdf", "Your prescription",
                  dry_run=False, meter=lambda: 1, phone_number_id="1", access_token="t",
                  post=make_post(captured))
    assert captured["payload"]["type"] == "document"
    assert captured["payload"]["document"] == {
        "link": "https://s3/rx.pdf?sig=1", "filename": "rx.pdf", "caption": "Your prescription",
    }


def test_budget_ceiling_refuses_before_posting(monkeypatch):
    monkeypatch.setattr(messaging, "MESSAGING_BUDGET", 2)
    posted = []
    kw = dict(dry_run=False, phone_number_id="1", access_token="t",
              post=lambda *a: posted.append(a) or {"messages": [{"id": "x"}]})
    send_text("+91", "1", meter=lambda: 1, **kw)
    send_text("+91", "2", meter=lambda: 2, **kw)
    with pytest.raises(MessagingBudgetExceeded):
        send_text("+91", "3", meter=lambda: 3, **kw)
    assert len(posted) == 2


def test_real_send_without_credentials_fails_fast(monkeypatch):
    monkeypatch.setattr(messaging, "WA_PHONE_NUMBER_ID", "")
    monkeypatch.setattr(messaging, "WA_ACCESS_TOKEN", "")
    with pytest.raises(MessagingError):
        send_text("+91", "x", dry_run=False, meter=lambda: 1)


def test_empty_body_and_bad_recipient_are_rejected():
    with pytest.raises(MessagingError):
        send_text("+91", "   ", dry_run=True)
    with pytest.raises(MessagingError):
        send_text("not-a-number", "hi", dry_run=True)


def test_http_failure_surfaces_as_api_error():
    def post(*_):
        raise MessagingAPIError("HTTP 401: bad token")

    with pytest.raises(MessagingAPIError):
        send_text("+91", "x", dry_run=False, meter=lambda: 1, phone_number_id="1",
                  access_token="t", post=post)


def test_http_post_sends_json_with_bearer(monkeypatch):
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"messages": [{"id": "wamid.1"}]}'

    def fake_urlopen(req, timeout, context=None):
        seen["auth"] = req.get_header("Authorization")
        seen["ctype"] = req.get_header("Content-type")
        seen["data"] = json.loads(req.data)
        return FakeResponse()

    monkeypatch.setattr(messaging.urllib.request, "urlopen", fake_urlopen)
    out = messaging._http_post_json("https://graph.facebook.com/x", {"a": 1}, "tok")
    assert seen == {"auth": "Bearer tok", "ctype": "application/json", "data": {"a": 1}}
    assert out["messages"][0]["id"] == "wamid.1"
