"""Outbound WhatsApp via the Twilio REST API, plus inbound signature validation.

Dry run is the default. The sandbox allows 100 messages for the whole hackathon,
so every real send is (a) an explicit `dry_run=False`, (b) counted against
MESSAGING_BUDGET in DynamoDB, and (c) logged with the running total. Nothing
in this module decides *whether* a message should go out - callers gate on
doctor approval (rule 7); this module only delivers.
"""
import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from core.http import ssl_context
from core.settings import (
    MESSAGING_BUDGET,
    MESSAGING_DRY_RUN,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_NUMBER,
)

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15
BUDGET_COUNTER = "whatsapp_sent"


class MessagingError(Exception):
    """Base class for every error raised by this module."""


class MessagingBudgetExceeded(MessagingError):
    """Refused: the sandbox message budget is spent."""


class MessagingAPIError(MessagingError):
    """Twilio rejected the request or was unreachable."""


def whatsapp_address(number: str) -> str:
    number = number.strip()
    return number if number.startswith("whatsapp:") else f"whatsapp:{number}"


def bare_number(address: str) -> str:
    """'whatsapp:+9198...' -> '+9198...'."""
    return address.split(":", 1)[1] if address.startswith("whatsapp:") else address


# -- inbound: X-Twilio-Signature ---------------------------------------------

def compute_signature(auth_token: str, url: str, params: dict) -> str:
    """Twilio's scheme: HMAC-SHA1 over the full URL followed by sorted key+value pairs."""
    payload = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def valid_signature(signature: str | None, url: str, params: dict, auth_token=None) -> bool:
    auth_token = auth_token or TWILIO_AUTH_TOKEN
    if not signature or not auth_token:
        return False
    return hmac.compare_digest(compute_signature(auth_token, url, params), signature)


# -- outbound -----------------------------------------------------------------

def _http_post_form(url: str, form: dict, account_sid: str, auth_token: str) -> dict:
    creds = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode(),
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=ssl_context()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise MessagingAPIError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MessagingAPIError(str(e)) from e


def send_whatsapp(
    to: str,
    body: str | None = None,
    media_url: str | None = None,
    *,
    content_sid: str | None = None,
    content_variables: dict | None = None,
    dry_run: bool | None = None,
    meter=None,
    account_sid: str | None = None,
    auth_token: str | None = None,
    from_number: str | None = None,
    post=_http_post_form,
) -> dict:
    """Send one WhatsApp message. Returns a small dict describing what happened.

    Free-form `body` is allowed only inside WhatsApp's 24 h window after the
    patient's last inbound message. Outside it (reminders), and always on a
    Twilio trial sender, pass an approved template's `content_sid` plus its
    `content_variables` instead.

    `meter` is a callable returning the running count of real sends after
    incrementing it; defaults to the DynamoDB counter. It runs *before* the send
    so a failed send still burns a slot - the budget is a ceiling, not a ledger.
    """
    if not body and not content_sid:
        raise MessagingError("either body or content_sid is required")
    dry_run = MESSAGING_DRY_RUN if dry_run is None else dry_run
    to_addr = whatsapp_address(to)
    described = {"to": to_addr, "body": body, "media_url": media_url,
                 "content_sid": content_sid, "content_variables": content_variables}
    if dry_run:
        log.info("DRY RUN whatsapp -> %s: %s%s%s", to_addr, body or "",
                 f" [template {content_sid} {content_variables or {}}]" if content_sid else "",
                 f" [media {media_url}]" if media_url else "")
        return {"dry_run": True, **described}

    account_sid = account_sid or TWILIO_ACCOUNT_SID
    auth_token = auth_token or TWILIO_AUTH_TOKEN
    from_number = from_number or TWILIO_WHATSAPP_NUMBER
    if not (account_sid and auth_token and from_number):
        raise MessagingError(
            "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER are required"
        )

    if meter is None:
        from core.store import Store

        meter = lambda: Store().increment_counter(BUDGET_COUNTER)  # noqa: E731
    count = meter()
    if count > MESSAGING_BUDGET:
        raise MessagingBudgetExceeded(f"send #{count} exceeds budget of {MESSAGING_BUDGET}")

    form = {"From": whatsapp_address(from_number), "To": to_addr}
    if content_sid:
        form["ContentSid"] = content_sid
        if content_variables:
            form["ContentVariables"] = json.dumps(content_variables)
    else:
        form["Body"] = body
    if media_url:
        form["MediaUrl"] = media_url
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    result = post(url, form, account_sid, auth_token)
    log.info("SENT whatsapp -> %s sid=%s status=%s (%d/%d used)",
             to_addr, result.get("sid"), result.get("status"), count, MESSAGING_BUDGET)
    return {
        "dry_run": False, **described,
        "sid": result.get("sid"), "status": result.get("status"), "sent_count": count,
    }
