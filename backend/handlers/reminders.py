"""Reminder fire handler, invoked by EventBridge Scheduler (spec step 12).

Input (set when the schedule was created):
  {"type": "dose", "rxId", "patientId", "brand_id", "label", "slot", "medIndex"}
  {"type": "next_visit", "rxId", "patientId", "next_visit"}

Free-form text is only allowed within 24 h of the patient's last inbound
message; outside that window WhatsApp requires an approved template, so the
handler picks the path per fire. The per-dose reminder row records sentAt, and
a second fire for the same slot (scheduler retry) is a no-op.
"""
import logging
from datetime import UTC, datetime

log = logging.getLogger()
log.setLevel(logging.INFO)

from handlers.api import _load_secrets_from_ssm  # noqa: E402

_load_secrets_from_ssm()

from core import messaging, reminders  # noqa: E402
from core.settings import WA_REMINDER_TEMPLATE  # noqa: E402
from core.store import Store, now_iso  # noqa: E402

_store = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def _row_for(event: dict, now_ist: datetime) -> dict | None:
    """Find today's planned row for this slot (deterministic id), if it exists."""
    key = event.get("brand_id") or f"m{event.get('medIndex', 0)}"
    rem_id = f"rem-{event['rxId']}-{key}-{now_ist.strftime('%Y%m%d')}-{event['slot']}"
    for r in store().list_reminders(event["patientId"]):
        if r["remId"] == rem_id:
            return r
    return None


def fire_dose(event: dict, now: datetime) -> dict:
    patient = store().get_patient(event["patientId"]) or {}
    if not patient.get("phone"):
        return {"skipped": "patient has no phone"}
    row = _row_for(event, now.astimezone(reminders.IST))
    if row and row.get("status") in ("SENT", "TAKEN"):
        return {"skipped": "already sent", "remId": row["remId"]}

    rx = None
    for r in store().list_prescriptions(event["patientId"]):
        if r["rxId"] == event["rxId"]:
            rx = r
            break
    if not rx:
        return {"skipped": "prescription not found"}
    med = next((m for m in rx.get("medicines", []) if m.get("brand_id") == event.get("brand_id")
                or m.get("label") == event.get("label")), None)
    if not med:
        return {"skipped": "medicine not on prescription"}

    first_name = (patient.get("name") or "there").split()[0]
    timing = messaging.plain_timing(med)
    if reminders.within_window(patient.get("lastInboundAt"), now):
        body = f"Hi {first_name}, " + reminders.dose_text(med)
        result = messaging.send_text(patient["phone"], body)
        path = "text"
    else:
        result = messaging.send_template(patient["phone"], WA_REMINDER_TEMPLATE,
                                         [first_name, med.get("label") or "", timing])
        path = "template"

    sent_at = now_iso()
    if row:
        store().set_reminder_fields(event["patientId"], row["dueAt"], row["remId"],
                                    status="SENT", sentAt=sent_at, sendPath=path)
    else:
        row = store().put_reminder(event["patientId"], {
            "remId": (f"rem-{event['rxId']}-{event.get('brand_id') or 'm'}-"
                      f"{now.strftime('%Y%m%d-%H%M')}"),
            "dueAt": sent_at, "rxId": event["rxId"], "brand_id": event.get("brand_id"),
            "label": med.get("label"), "slot": event.get("slot"), "status": "SENT",
            "sentAt": sent_at, "sendPath": path, "text": reminders.dose_text(med),
        })
    return {"sent": True, "path": path, "remId": row["remId"], "result": result}


def fire_next_visit(event: dict, now: datetime) -> dict:
    patient = store().get_patient(event["patientId"]) or {}
    if not patient.get("phone"):
        return {"skipped": "patient has no phone"}
    rxs = store().list_prescriptions(event["patientId"])
    rx = next((r for r in rxs if r["rxId"] == event["rxId"]), None)
    if rx and rx.get("visitReminderSentAt"):
        return {"skipped": "already sent"}
    doctor = store().get_doctor(rx["doctorId"]) if rx else None
    who = (doctor or {}).get("name", "your doctor")
    first_name = (patient.get("name") or "there").split()[0]
    body = (f"Hi {first_name}, your follow-up visit with {who} is due today. "
            "Reply if you need to reschedule.")
    if reminders.within_window(patient.get("lastInboundAt"), now):
        result = messaging.send_text(patient["phone"], body)
    else:
        result = messaging.send_template(patient["phone"], WA_REMINDER_TEMPLATE,
                                         [first_name, f"follow-up with {who}", "today"])
    if rx:
        store().put_prescription(rx["patientId"], rx["doctorId"],
                                 {**rx, "visitReminderSentAt": now_iso()})
    return {"sent": True, "result": result}


def handler(event, context):
    now = datetime.now(UTC)
    kind = event.get("type")
    log.info("reminder fire type=%s rx=%s patient=%s", kind, event.get("rxId"),
             event.get("patientId"))
    if kind == "dose":
        return fire_dose(event, now)
    if kind == "next_visit":
        return fire_next_visit(event, now)
    raise ValueError(f"unknown reminder type {kind!r}")
