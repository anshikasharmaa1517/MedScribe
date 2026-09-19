"""Outbound WhatsApp. Provider-agnostic; DRY_RUN is on by default.

Rule 7 lives upstream (only the approval pipeline calls this), but the dry-run
default is a second guard: nothing leaves the building unless someone has
deliberately set DRY_RUN=false in the deployed environment.

Providers:
  meta   - WhatsApp Cloud API (Graph): WA_PHONE_NUMBER_ID + WA_ACCESS_TOKEN
  twilio - not implemented yet; the interface is the same two calls
"""
import json
import logging
import urllib.error
import urllib.request

from core.settings import (
    DRY_RUN,
    WA_ACCESS_TOKEN,
    WA_API_VERSION,
    WA_PHONE_NUMBER_ID,
    WA_PROVIDER,
)

log = logging.getLogger(__name__)


class MessagingError(Exception):
    """The provider rejected the message or could not be reached."""


def _graph_post(payload: dict) -> dict:
    if not (WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN):
        raise MessagingError("WA_PHONE_NUMBER_ID / WA_ACCESS_TOKEN not configured")
    url = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_NUMBER_ID}/messages"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {WA_ACCESS_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise MessagingError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MessagingError(str(e)) from e


def _to_wa(phone_e164: str) -> str:
    return phone_e164.lstrip("+")


def _send(payload: dict, *, dry_run: bool | None = None) -> dict:
    dry = DRY_RUN if dry_run is None else dry_run
    if dry:
        log.info("DRY_RUN whatsapp -> %s: %s", payload.get("to"), json.dumps(payload)[:300])
        return {"dry_run": True, "provider": WA_PROVIDER, "to": payload.get("to"),
                "payload": payload}
    if WA_PROVIDER != "meta":
        raise MessagingError(f"provider {WA_PROVIDER!r} not implemented")
    out = _graph_post(payload)
    message_id = (out.get("messages") or [{}])[0].get("id")
    return {"dry_run": False, "provider": "meta", "to": payload.get("to"), "message_id": message_id}


def send_text(to_e164: str, body: str, *, dry_run: bool | None = None) -> dict:
    return _send({
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": _to_wa(to_e164),
        "type": "text", "text": {"preview_url": False, "body": body},
    }, dry_run=dry_run)


def send_document(to_e164: str, url: str, filename: str, caption: str = "",
                  *, dry_run: bool | None = None) -> dict:
    doc = {"link": url, "filename": filename}
    if caption:
        doc["caption"] = caption
    return _send({
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": _to_wa(to_e164),
        "type": "document", "document": doc,
    }, dry_run=dry_run)


def send_template(to_e164: str, name: str, params: list[str], lang: str = "en",
                  *, dry_run: bool | None = None) -> dict:
    return _send({
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": _to_wa(to_e164),
        "type": "template",
        "template": {
            "name": name, "language": {"code": lang},
            "components": [{"type": "body",
                            "parameters": [{"type": "text", "text": p} for p in params]}],
        },
    }, dry_run=dry_run)


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
