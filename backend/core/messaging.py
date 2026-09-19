"""Outbound WhatsApp via the Meta Cloud API, plus inbound webhook signature checks.

Dry run is the default: every real send is an explicit `dry_run=False`, is
counted against MESSAGING_BUDGET in DynamoDB, and is logged with the running
total. Nothing in this module decides *whether* a message should go out -
callers gate on doctor approval (rule 7); this module only delivers.

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
    MESSAGING_BUDGET,
    MESSAGING_DRY_RUN,
    WA_ACCESS_TOKEN,
    WA_APP_SECRET,
    WA_GRAPH_VERSION,
    WA_PHONE_NUMBER_ID,
)

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15
BUDGET_COUNTER = "whatsapp_sent"
GRAPH_BASE = "https://graph.facebook.com"


class MessagingError(Exception):
    """Base class for every error raised by this module."""


class MessagingBudgetExceeded(MessagingError):
    """Refused: the message budget is spent."""


class MessagingAPIError(MessagingError):
    """Meta rejected the request or was unreachable."""


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
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=ssl_context()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise MessagingAPIError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MessagingAPIError(str(e)) from e


def _deliver(
    to: str,
    payload: dict,
    summary: str,
    *,
    dry_run: bool | None,
    meter,
    phone_number_id: str | None,
    access_token: str | None,
    post,
) -> dict:
    dry_run = MESSAGING_DRY_RUN if dry_run is None else dry_run
    to_id = wa_id(to)
    if not to_id:
        raise MessagingError(f"invalid recipient {to!r}")
    body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to_id, **payload}
    described = {"to": e164(to_id), "type": payload["type"], "summary": summary}
    if dry_run:
        log.info("DRY RUN whatsapp -> %s: %s", described["to"], summary)
        return {"dry_run": True, **described}

    phone_number_id = phone_number_id or WA_PHONE_NUMBER_ID
    access_token = access_token or WA_ACCESS_TOKEN
    if not (phone_number_id and access_token):
        raise MessagingError("WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN are required")

    if meter is None:
        from core.store import Store

        meter = lambda: Store().increment_counter(BUDGET_COUNTER)  # noqa: E731
    count = meter()
    if count > MESSAGING_BUDGET:
        raise MessagingBudgetExceeded(f"send #{count} exceeds budget of {MESSAGING_BUDGET}")

    url = f"{GRAPH_BASE}/{WA_GRAPH_VERSION}/{phone_number_id}/messages"
    result = post(url, body, access_token)
    message_id = ((result.get("messages") or [{}])[0]).get("id")
    log.info("SENT whatsapp -> %s id=%s (%d/%d used): %s",
             described["to"], message_id, count, MESSAGING_BUDGET, summary)
    return {"dry_run": False, **described, "message_id": message_id, "sent_count": count}


def send_text(to: str, body: str, *, dry_run=None, meter=None, phone_number_id=None,
              access_token=None, post=_http_post_json) -> dict:
    if not body or not body.strip():
        raise MessagingError("body is required")
    payload = {"type": "text", "text": {"preview_url": False, "body": body}}
    return _deliver(to, payload, body, dry_run=dry_run, meter=meter,
                    phone_number_id=phone_number_id, access_token=access_token, post=post)


def send_template(to: str, name: str, language: str = "en", body_params: list[str] | None = None,
                  components: list[dict] | None = None, *, dry_run=None, meter=None,
                  phone_number_id=None, access_token=None, post=_http_post_json) -> dict:
    """Send an approved template. `body_params` fills {{1}}..{{n}} in order; pass
    `components` instead for headers/buttons."""
    template = {"name": name, "language": {"code": language}}
    if components is None and body_params:
        components = [{"type": "body",
                       "parameters": [{"type": "text", "text": str(p)} for p in body_params]}]
    if components:
        template["components"] = components
    payload = {"type": "template", "template": template}
    summary = f"template {name}/{language} {body_params or ''}".strip()
    return _deliver(to, payload, summary, dry_run=dry_run, meter=meter,
                    phone_number_id=phone_number_id, access_token=access_token, post=post)


def send_document(to: str, link: str, filename: str, caption: str | None = None, *,
                  dry_run=None, meter=None, phone_number_id=None, access_token=None,
                  post=_http_post_json) -> dict:
    document = {"link": link, "filename": filename}
    if caption:
        document["caption"] = caption
    payload = {"type": "document", "document": document}
    return _deliver(to, payload, f"document {filename} {link}", dry_run=dry_run, meter=meter,
                    phone_number_id=phone_number_id, access_token=access_token, post=post)


def send_whatsapp(to: str, body: str, **kw) -> dict:
    """Plain-text convenience used by the inbound router."""
    return send_text(to, body, **kw)
