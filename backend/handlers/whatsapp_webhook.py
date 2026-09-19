"""/webhooks/whatsapp - Meta Cloud API webhook.

GET  is Meta's one-time verification handshake (hub.verify_token / hub.challenge).
POST carries inbound messages and delivery statuses. It is unauthenticated by
necessity, so X-Hub-Signature-256 (HMAC of the raw body with the app secret) is
verified before the body is parsed. Meta expects a fast 200 and will redeliver
on timeout, so the slow work runs in a second, asynchronous invocation of this
function, and each message id is claimed in DynamoDB so a redelivery is a no-op.
"""
import base64
import json
import logging
import os

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)


def _load_secrets_from_ssm() -> None:
    prefix = os.environ.get("SSM_PREFIX")
    if not prefix or os.environ.get("SECRETS_LOADED"):
        return
    try:
        ssm = boto3.client("ssm")
        token = None
        while True:
            kwargs = {"Path": prefix, "WithDecryption": True}
            if token:
                kwargs["NextToken"] = token
            page = ssm.get_parameters_by_path(**kwargs)
            for p in page.get("Parameters", []):
                os.environ.setdefault(p["Name"].rsplit("/", 1)[-1], p["Value"])
            token = page.get("NextToken")
            if not token:
                break
        os.environ["SECRETS_LOADED"] = "1"
    except Exception as e:  # noqa: BLE001 - without the secret every POST is a 403, which is correct
        log.warning("could not load SSM secrets: %s", e)


_load_secrets_from_ssm()

from core import inbound  # noqa: E402
from core.messaging import e164, valid_signature  # noqa: E402
from core.settings import WA_VERIFY_TOKEN  # noqa: E402

ASYNC_KEY = "medscribe_inbound"

_store = None


def store():
    global _store
    if _store is None:
        from core.store import Store

        _store = Store()
    return _store


def respond(status: int, body: str = "", content_type: str = "text/plain") -> dict:
    return {"statusCode": status, "headers": {"Content-Type": content_type}, "body": body}


def raw_body(event: dict) -> bytes:
    raw = event.get("body") or ""
    return base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode()


def verify(event: dict) -> dict:
    q = event.get("queryStringParameters") or {}
    token_ok = bool(WA_VERIFY_TOKEN) and q.get("hub.verify_token") == WA_VERIFY_TOKEN
    if q.get("hub.mode") == "subscribe" and token_ok:
        log.info("webhook verification handshake accepted")
        return respond(200, q.get("hub.challenge", ""))
    log.warning("webhook verification rejected")
    return respond(403, "verification failed")


def extract_messages(payload: dict) -> list[dict]:
    """Flatten Meta's entry/changes/value envelope into provider-neutral messages."""
    out = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if value.get("messaging_product") != "whatsapp":
                continue
            for status in value.get("statuses") or []:
                log.info("status %s for %s -> %s", status.get("status"), status.get("id"),
                         status.get("recipient_id"))
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name")
                     for c in value.get("contacts") or []}
            for m in value.get("messages") or []:
                out.append(neutral_message(m, names.get(m.get("from"))))
    return out


def neutral_message(m: dict, profile_name: str | None) -> dict:
    kind = m.get("type")
    body, button = "", None
    if kind == "text":
        body = (m.get("text") or {}).get("body", "")
    elif kind == "button":  # template quick-reply
        button = (m.get("button") or {}).get("payload")
        body = (m.get("button") or {}).get("text", "")
    elif kind == "interactive":
        reply = (m.get("interactive") or {}).get("button_reply") or \
                (m.get("interactive") or {}).get("list_reply") or {}
        button, body = reply.get("id"), reply.get("title", "")
    else:  # audio, image, document, location, sticker, ...
        body = f"[{kind}]"
    return {
        "From": e164(m.get("from", "")),
        "Body": body,
        "ProfileName": profile_name,
        "ButtonPayload": button,
        "MessageSid": m.get("id"),
        "Type": kind,
        "Timestamp": m.get("timestamp"),
    }


def dispatch_async(messages: list[dict]) -> bool:
    """Re-invoke this function with the parsed messages; returns False when not on Lambda."""
    name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    if not name:
        return False
    boto3.client("lambda").invoke(
        FunctionName=name, InvocationType="Event", Payload=json.dumps({ASYNC_KEY: messages})
    )
    return True


def process_all(messages: list[dict]) -> list[dict]:
    results = []
    for msg in messages:
        if msg.get("MessageSid") and not store().claim_inbound(msg["MessageSid"]):
            log.info("duplicate delivery of %s ignored", msg["MessageSid"])
            continue
        results.append(inbound.process_inbound(msg, store=store()))
    log.info("inbound processed: %s", json.dumps(results, default=str))
    return results


def handler(event, context):
    if ASYNC_KEY in event:
        return process_all(event[ASYNC_KEY])

    method = (event.get("requestContext", {}).get("http") or {}).get("method", "").upper()
    if method == "GET":
        return verify(event)

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body = raw_body(event)
    if not valid_signature(headers.get("x-hub-signature-256"), body):
        log.warning("rejected webhook POST with bad signature")
        return respond(403, "bad signature")

    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return respond(400, "invalid JSON")

    messages = extract_messages(payload)
    if messages:
        log.info("inbound %d message(s) from %s", len(messages),
                 ", ".join(m["From"] for m in messages))
        if not dispatch_async(messages):
            process_all(messages)
    return respond(200, "OK")
