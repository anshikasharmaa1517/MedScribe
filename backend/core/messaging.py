"""WhatsApp via the Meta Cloud API: outbound sends, inbound signature check, message text.

DRY_RUN is on by default. Rule 7 lives upstream (only the approval pipeline and
the inbound router call this), but the dry-run default is a second guard:
nothing leaves the building unless someone has deliberately set DRY_RUN=false in
the deployed environment. Real sends are counted against MESSAGING_BUDGET in
DynamoDB and logged with the running total.

Free-form text and documents are accepted by WhatsApp only inside the 24 h
window after the patient's last inbound message. Outside it (reminders), send
an approved template.
"""
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from core.http import ssl_context
from core.settings import (
    DRY_RUN,
    MESSAGING_BUDGET,
    WA_ACCESS_TOKEN,
    WA_API_VERSION,
    WA_APP_SECRET,
    WA_PHONE_NUMBER_ID,
    WA_PROVIDER,
)

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
BUDGET_COUNTER = "whatsapp_sent"
GRAPH_BASE = "https://graph.facebook.com"


class MessagingError(Exception):
    """Base class for every error raised by this module."""


class MessagingBudgetExceeded(MessagingError):
    """Refused: the message budget is spent."""


class MessagingAPIError(MessagingError):
    """The provider rejected the message or could not be reached."""


def wa_id(phone: str) -> str:
    """E.164 '+919876543210' -> WhatsApp id '919876543210'."""
    return "".join(ch for ch in phone if ch.isdigit())


def e164(wa: str) -> str:
    """WhatsApp id '919876543210' -> '+919876543210'."""
    digits = wa_id(wa)
    return f"+{digits}" if digits else ""


# -- inbound: X-Hub-Signature-256 ---------------------------------------------

def compute_signature(app_secret: str, raw_body: bytes) -> str:
    return "sha256=" + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()


def valid_signature(header: str | None, raw_body: bytes, app_secret: str | None = None) -> bool:
    app_secret = app_secret or WA_APP_SECRET
    if not header or not app_secret:
        return False
    return hmac.compare_digest(compute_signature(app_secret, raw_body), header)


# -- outbound -----------------------------------------------------------------

def _http_post_json(url: str, payload: dict, access_token: str) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=ssl_context()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise MessagingAPIError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MessagingAPIError(str(e)) from e


def _send(payload: dict, summary: str, *, dry_run=None, meter=None, phone_number_id=None,
          access_token=None, post=_http_post_json) -> dict:
    """Deliver one message. `meter` returns the running count of real sends after
    incrementing it (defaults to the DynamoDB counter); it runs before the send, so a
    failed send still burns a slot - the budget is a ceiling, not a ledger."""
    dry = DRY_RUN if dry_run is None else dry_run
    described = {"provider": WA_PROVIDER, "to": e164(payload["to"]), "type": payload["type"],
                 "summary": summary}
    if dry:
        log.info("DRY_RUN whatsapp -> %s: %s", described["to"], summary)
        return {"dry_run": True, **described, "payload": payload}
    if WA_PROVIDER != "meta":
        raise MessagingError(f"provider {WA_PROVIDER!r} not implemented")

    phone_number_id = phone_number_id or WA_PHONE_NUMBER_ID
    access_token = access_token or WA_ACCESS_TOKEN
    if not (phone_number_id and access_token):
        raise MessagingError("WA_PHONE_NUMBER_ID / WA_ACCESS_TOKEN not configured")

    if meter is None:
        from core.store import Store

        meter = lambda: Store().increment_counter(BUDGET_COUNTER)  # noqa: E731
    count = meter()
    if count > MESSAGING_BUDGET:
        raise MessagingBudgetExceeded(f"send #{count} exceeds budget of {MESSAGING_BUDGET}")

    url = f"{GRAPH_BASE}/{WA_API_VERSION}/{phone_number_id}/messages"
    out = post(url, payload, access_token)
    message_id = (out.get("messages") or [{}])[0].get("id")
    log.info("SENT whatsapp -> %s id=%s (%d/%d used): %s",
             described["to"], message_id, count, MESSAGING_BUDGET, summary)
    return {"dry_run": False, **described, "message_id": message_id, "sent_count": count}


def _envelope(to_e164: str, body: dict) -> dict:
    to = wa_id(to_e164)
    if not to:
        raise MessagingError(f"invalid recipient {to_e164!r}")
    return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, **body}


def send_text(to_e164: str, body: str, *, dry_run=None, **kw) -> dict:
    if not body or not body.strip():
        raise MessagingError("body is required")
    payload = _envelope(to_e164, {"type": "text", "text": {"preview_url": False, "body": body}})
    return _send(payload, body, dry_run=dry_run, **kw)


def send_document(to_e164: str, url: str, filename: str, caption: str = "",
                  *, dry_run=None, **kw) -> dict:
    doc = {"link": url, "filename": filename}
    if caption:
        doc["caption"] = caption
    payload = _envelope(to_e164, {"type": "document", "document": doc})
    return _send(payload, f"document {filename} {url}", dry_run=dry_run, **kw)


def send_template(to_e164: str, name: str, params: list[str] | None = None, lang: str = "en",
                  *, components: list[dict] | None = None, dry_run=None, **kw) -> dict:
    """Send an approved template. `params` fills {{1}}..{{n}} of the body in order;
    pass `components` instead for headers/buttons."""
    template = {"name": name, "language": {"code": lang}}
    if components is None and params:
        components = [{"type": "body",
                       "parameters": [{"type": "text", "text": str(p)} for p in params]}]
    if components:
        template["components"] = components
    payload = _envelope(to_e164, {"type": "template", "template": template})
    return _send(payload, f"template {name}/{lang} {params or ''}".strip(), dry_run=dry_run, **kw)


def send_whatsapp(to_e164: str, body: str, **kw) -> dict:
    """Plain-text convenience used by the inbound router."""
    return send_text(to_e164, body, **kw)


# -- message text --------------------------------------------------------------

FREQ_WORDS = {
    "1-0-0": "1 tablet morning", "0-1-0": "1 tablet afternoon", "0-0-1": "1 tablet night",
    "1-0-1": "1 tablet morning, 1 tablet night", "1-1-1": "1 tablet morning, afternoon and night",
    "1-1-0": "1 tablet morning, 1 tablet afternoon", "0-1-1": "1 tablet afternoon, 1 tablet night",
    "OD": "once a day", "BD": "twice a day", "TDS": "three times a day",
    "QID": "four times a day", "HS": "at bedtime", "SOS": "only when needed", "stat": "once, now",
}


def plain_timing(med: dict) -> str:
    parts = [FREQ_WORDS.get(str(med.get("frequency") or ""), med.get("frequency") or "")]
    if med.get("food_relation"):
        parts.append(med["food_relation"])
    if med.get("duration"):
        parts.append(f"for {med['duration']}")
    return ", ".join(p for p in parts if p)


def prescription_text(rx: dict, doctor: dict) -> str:
    who = doctor.get("name", "your doctor")
    clinic = f" ({doctor['clinic']})" if doctor.get("clinic") else ""
    lines = [f"Prescription from {who}{clinic}:"]
    if rx.get("diagnosis"):
        lines.append(f"Diagnosis: {rx['diagnosis']}")
    lines.append("")
    for i, m in enumerate(rx.get("medicines", []), start=1):
        lines.append(f"{i}. {m.get('label') or m.get('spoken')} - {plain_timing(m)}")
    if rx.get("tests_advised"):
        lines += ["", "Tests: " + ", ".join(rx["tests_advised"])]
    if rx.get("next_visit"):
        lines += ["", f"Next visit: {rx['next_visit']}"]
    lines += ["", "The full prescription PDF is attached above. Reply TAKEN after each dose."]
    return "\n".join(lines)


def generics_text(alternatives: dict[str, list[str]]) -> str:
    """alternatives: prescribed label -> other brand labels with the same salt(s)."""
    if not any(alternatives.values()):
        return ""
    lines = ["Same-salt alternatives (ask your pharmacist - the salt is what matters):", ""]
    for label, alts in alternatives.items():
        if alts:
            lines.append(f"{label}: {', '.join(alts[:4])}")
    lines += ["", "This is information only, not a prescription change."]
    return "\n".join(lines)
