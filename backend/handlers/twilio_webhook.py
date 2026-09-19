"""POST /webhooks/twilio - inbound WhatsApp.

Unauthenticated by necessity, so X-Twilio-Signature is verified before the
body is looked at. Twilio expects a fast 200; anything slow (DynamoDB, a
reply) happens in a second, asynchronous invocation of this same function.
"""
import base64
import json
import logging
import os
import urllib.parse

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
    except Exception as e:  # noqa: BLE001 - without the token every request is a 403, which is correct
        log.warning("could not load SSM secrets: %s", e)


_load_secrets_from_ssm()

from core import inbound  # noqa: E402
from core.messaging import valid_signature  # noqa: E402

ASYNC_KEY = "medscribe_inbound"
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

_store = None


def store():
    global _store
    if _store is None:
        from core.store import Store

        _store = Store()
    return _store


def respond(status: int, body: str = EMPTY_TWIML) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "text/xml"}, "body": body}


def request_url(event: dict) -> str:
    """The URL Twilio signed: scheme + host + path as Twilio sees it, including the stage."""
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    host = headers.get("host", "")
    path = event.get("rawPath") or event.get("requestContext", {}).get("http", {}).get("path", "")
    url = f"https://{host}{path}"
    if event.get("rawQueryString"):
        url += "?" + event["rawQueryString"]
    return url


def form_params(event: dict) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}


def dispatch_async(message: dict) -> bool:
    """Re-invoke this function with the parsed message; returns False when not on Lambda."""
    name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    if not name:
        return False
    boto3.client("lambda").invoke(
        FunctionName=name, InvocationType="Event", Payload=json.dumps({ASYNC_KEY: message})
    )
    return True


def handler(event, context):
    if ASYNC_KEY in event:
        result = inbound.process_inbound(event[ASYNC_KEY], store=store())
        log.info("inbound processed: %s", json.dumps(result, default=str))
        return result

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    params = form_params(event)
    if not valid_signature(headers.get("x-twilio-signature"), request_url(event), params):
        log.warning("rejected inbound with bad signature from %s", params.get("From"))
        return respond(403, "")

    message = {k: params.get(k) for k in ("From", "Body", "ProfileName", "ButtonPayload",
                                          "MessageSid", "WaId")}
    log.info("inbound from %s sid=%s", message["From"], message["MessageSid"])
    if not dispatch_async(message):
        inbound.process_inbound(message, store=store())
    return respond(200)
