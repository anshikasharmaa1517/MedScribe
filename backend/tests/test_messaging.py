import base64
import itertools

import pytest

from core import messaging
from core.messaging import (
    MessagingAPIError,
    MessagingBudgetExceeded,
    MessagingError,
    bare_number,
    compute_signature,
    send_whatsapp,
    valid_signature,
    whatsapp_address,
)

# The worked example from Twilio's security docs; expected value computed with the
# official twilio SDK's RequestValidator so this pins parity with their algorithm.
DOC_TOKEN = "12345"
DOC_URL = "https://mycompany.com/myapp.php?foo=1&bar=2"
DOC_PARAMS = {
    "CallSid": "CA1234567890ABCDE",
    "Caller": "+12349013030",
    "Digits": "1234",
    "From": "+12349013030",
    "To": "+18005551212",
}
DOC_SIGNATURE = "0/KCTR6DLpKmkAf8muzZqo1nDgQ="


def test_signature_matches_twilio_sdk():
    assert compute_signature(DOC_TOKEN, DOC_URL, DOC_PARAMS) == DOC_SIGNATURE
    assert valid_signature(DOC_SIGNATURE, DOC_URL, DOC_PARAMS, auth_token=DOC_TOKEN)


def test_signature_rejects_tampering_and_missing_inputs():
    assert not valid_signature(DOC_SIGNATURE, DOC_URL, {**DOC_PARAMS, "Digits": "9"}, DOC_TOKEN)
    assert not valid_signature(DOC_SIGNATURE, DOC_URL + "x", DOC_PARAMS, DOC_TOKEN)
    assert not valid_signature(None, DOC_URL, DOC_PARAMS, DOC_TOKEN)
    assert not valid_signature(DOC_SIGNATURE, DOC_URL, DOC_PARAMS, auth_token="")


def test_address_helpers():
    assert whatsapp_address("+919800000001") == "whatsapp:+919800000001"
    assert whatsapp_address("whatsapp:+919800000001") == "whatsapp:+919800000001"
    assert bare_number("whatsapp:+919800000001") == "+919800000001"
    assert bare_number("+919800000001") == "+919800000001"


def test_dry_run_is_the_default_and_never_posts(monkeypatch):
    calls = []
    monkeypatch.setattr(messaging, "MESSAGING_DRY_RUN", True)
    out = send_whatsapp("+919800000001", "hi", post=lambda *a: calls.append(a))
    assert out["dry_run"] is True
    assert out["to"] == "whatsapp:+919800000001"
    assert calls == []


def test_real_send_posts_form_with_basic_auth_and_counts():
    captured = {}

    def post(url, form, sid, token):
        captured.update(url=url, form=form, sid=sid, token=token)
        return {"sid": "SM123", "status": "queued"}

    counter = itertools.count(1)
    out = send_whatsapp(
        "+919800000001", "hello", media_url="https://x/y.pdf", dry_run=False,
        meter=lambda: next(counter), account_sid="AC1", auth_token="tok",
        from_number="+14155238886", post=post,
    )
    assert captured["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json"
    assert captured["form"] == {
        "From": "whatsapp:+14155238886", "To": "whatsapp:+919800000001",
        "Body": "hello", "MediaUrl": "https://x/y.pdf",
    }
    assert (captured["sid"], captured["token"]) == ("AC1", "tok")
    assert out == {
        "dry_run": False, "to": "whatsapp:+919800000001", "body": "hello",
        "media_url": "https://x/y.pdf", "sid": "SM123", "status": "queued", "sent_count": 1,
    }


def test_budget_ceiling_refuses_before_posting(monkeypatch):
    monkeypatch.setattr(messaging, "MESSAGING_BUDGET", 2)
    posted = []
    kw = dict(dry_run=False, account_sid="AC1", auth_token="tok", from_number="+1",
              post=lambda *a: posted.append(a) or {"sid": "x", "status": "queued"})
    send_whatsapp("+91", "1", meter=lambda: 1, **kw)
    send_whatsapp("+91", "2", meter=lambda: 2, **kw)
    with pytest.raises(MessagingBudgetExceeded):
        send_whatsapp("+91", "3", meter=lambda: 3, **kw)
    assert len(posted) == 2


def test_real_send_without_credentials_fails_fast(monkeypatch):
    monkeypatch.setattr(messaging, "TWILIO_ACCOUNT_SID", "")
    monkeypatch.setattr(messaging, "TWILIO_AUTH_TOKEN", "")
    with pytest.raises(MessagingError):
        send_whatsapp("+91", "x", dry_run=False, meter=lambda: 1)


def test_http_failure_surfaces_as_api_error():
    def post(*_):
        raise MessagingAPIError("HTTP 401: bad creds")

    with pytest.raises(MessagingAPIError):
        send_whatsapp("+91", "x", dry_run=False, meter=lambda: 1, account_sid="a",
                      auth_token="b", from_number="+1", post=post)


def test_basic_auth_header_is_well_formed(monkeypatch):
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"sid": "SM1", "status": "queued"}'

    def fake_urlopen(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        seen["data"] = req.data
        return FakeResponse()

    monkeypatch.setattr(messaging.urllib.request, "urlopen", fake_urlopen)
    messaging._http_post_form("https://api.twilio.com/x", {"Body": "a b"}, "AC1", "tok")
    assert seen["auth"] == "Basic " + base64.b64encode(b"AC1:tok").decode()
    assert seen["data"] == b"Body=a+b"
