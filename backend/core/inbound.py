"""Routes one inbound WhatsApp message to what it means for the patient record.

Three intents (spec §8):
  START <doctorId>  - the QR scan. Links patient <-> doctor, records consent.
  TAKEN / 1         - acknowledges the most recent reminder that was sent.
  anything else     - the history chat (step 13). Stubbed: logged, no reply.

Replies go back through messaging.send_whatsapp, which is dry-run by default.
A reply to an inbound message is inside Twilio's 24 h window, so free-form
text is allowed here; reminders fired later are not and need a template.
"""
import logging
import re

from core import messaging
from core.store import Store, now_iso

log = logging.getLogger(__name__)

START_RE = re.compile(r"\bSTART\s+([A-Za-z0-9_\-]+)", re.IGNORECASE)
TAKEN_WORDS = {"taken", "1", "done", "le liya", "le li"}

CONSENT_SCOPES = ["recording", "storage", "whatsapp_delivery", "reminders"]


def classify(body: str, button_payload: str | None = None) -> tuple[str, str | None]:
    """-> ('start', doctorId) | ('taken', None) | ('chat', None)."""
    text = (body or "").strip()
    m = START_RE.search(text)
    if m:
        return "start", m.group(1)
    if (button_payload or "").strip().lower() in TAKEN_WORDS or text.lower() in TAKEN_WORDS:
        return "taken", None
    return "chat", None


def handle_start(store: Store, phone: str, doctor_id: str, profile_name: str | None) -> str:
    doctor = store.get_doctor(doctor_id)
    if not doctor:
        log.warning("START for unknown doctor %s from %s", doctor_id, phone)
        return "Sorry, that clinic code was not recognised. Please ask the clinic for a new QR."

    patient = store.get_patient_by_phone(phone) or {"phone": phone}
    patient = store.put_patient({
        **patient,
        "name": patient.get("name") or profile_name or phone,
        "doctorId": doctor_id,
        "consent": {"scopes": CONSENT_SCOPES, "at": now_iso(), "via": "whatsapp_start"},
        "waLinkedAt": now_iso(),
        "lastInboundAt": now_iso(),
    })
    log.info("linked patient %s (%s) to doctor %s", patient["patientId"], phone, doctor_id)
    return (
        f"Namaste {patient['name']}! You are now linked with {doctor['name']}"
        f"{' at ' + doctor['clinic'] if doctor.get('clinic') else ''}. "
        "Your prescription will arrive here after the consultation, and we'll remind you "
        "when each medicine is due. Reply TAKEN to a reminder once you've taken it."
    )


def handle_taken(store: Store, phone: str) -> str | None:
    patient = store.get_patient_by_phone(phone)
    if not patient:
        log.info("TAKEN from unknown number %s", phone)
        return None
    store.put_patient({**patient, "lastInboundAt": now_iso()})
    pending = [r for r in store.list_reminders(patient["patientId"]) if r.get("status") == "SENT"]
    if not pending:
        log.info("TAKEN from %s but no reminder pending", patient["patientId"])
        return "No reminder is waiting for a reply right now."
    latest = max(pending, key=lambda r: r.get("sentAt") or r["dueAt"])
    store.mark_reminder_taken(latest)
    log.info("reminder %s marked TAKEN for %s", latest["remId"], patient["patientId"])
    label = latest.get("label") or latest.get("brand_id") or "your medicine"
    return f"Noted - {label} marked as taken. Take care!"


def handle_chat(store: Store, phone: str, body: str) -> None:
    patient = store.get_patient_by_phone(phone)
    if patient:
        store.put_patient({**patient, "lastInboundAt": now_iso()})
    log.info("history chat (stub) from %s: %r", phone, body[:200])
    return None


def process_inbound(message: dict, store: Store | None = None, send=None) -> dict:
    """`message` is the parsed Twilio form: From, Body, ProfileName, ButtonPayload, MessageSid."""
    store = store or Store()
    send = send or messaging.send_whatsapp
    phone = messaging.bare_number(message.get("From", ""))
    body = message.get("Body", "")
    intent, doctor_id = classify(body, message.get("ButtonPayload"))

    if intent == "start":
        reply = handle_start(store, phone, doctor_id, message.get("ProfileName"))
    elif intent == "taken":
        reply = handle_taken(store, phone)
    else:
        reply = handle_chat(store, phone, body)

    result = {"intent": intent, "phone": phone, "replied": bool(reply)}
    if reply:
        result["send"] = send(phone, reply)
    return result
