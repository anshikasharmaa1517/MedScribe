"""Post-approval pipeline (spec §3), one Lambda dispatching on `step`.

FinalTranscript -> FinalExtract -> ResolveValidate -> [gate] -> RenderPDF ->
PersistRx -> SendPrescription -> SendGenerics -> ScheduleReminders

Every step is idempotent on rxId so a Step Functions retry cannot double-send
or double-write: prescriptions are keyed on a fixed approvedAt, sends check the
stored sentAt first, reminders use deterministic ids.
"""
import logging
from datetime import UTC, datetime, timedelta, timezone

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

from handlers.api import _load_secrets_from_ssm  # noqa: E402

_load_secrets_from_ssm()

from core import messaging, pdf  # noqa: E402
from core.llm import get_client  # noqa: E402
from core.pipeline import empty_draft, process, validate_state  # noqa: E402
from core.reference import brand_label, default_reference  # noqa: E402
from core.settings import MEDSCRIBE_BUCKET  # noqa: E402
from core.store import Store, now_iso  # noqa: E402

_store = None
_client = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def llm():
    global _client
    if _client is None:
        _client = get_client()
    return _client


class PipelineError(Exception):
    pass


def _consult(inp: dict) -> dict:
    c = store().get_consult_by_id(inp["consultId"])
    if not c:
        raise PipelineError(f"consult {inp['consultId']} not found")
    return c


def transcript_text(c: dict) -> str:
    label = {"doctor": "Doctor", "patient": "Patient"}
    out = []
    for line in c.get("transcript", []):
        who = label.get(line.get("speaker"))
        out.append(f"{who}: {line['text']}" if who else line["text"])
    return "\n".join(out)


# -- steps ---------------------------------------------------------------------

def final_transcript(inp: dict) -> dict:
    c = _consult(inp)
    if c.get("status") == "APPROVED":
        return {**inp, "alreadyApproved": True, "rxId": c.get("rxId", inp.get("rxId"))}
    store().update_consultation(c, status="APPROVING", approvalStartedAt=inp.get("approvedAt"))
    return {**inp, "transcriptText": transcript_text(c), "lineCount": len(c.get("transcript", []))}


def final_extract(inp: dict) -> dict:
    c = _consult(inp)
    prior = c.get("draft") or empty_draft()
    if not inp.get("transcriptText"):
        return {**inp, "draft": prior}
    draft = process(inp["transcriptText"], prior, client=llm())
    if draft.get("extraction_error"):
        # Prior good state is authoritative enough for approval; the live pass already showed it.
        log.warning("final extraction failed, using live draft: %s", draft["extraction_error"])
        draft = {**prior}
        draft.pop("extraction_error", None)
    return {**inp, "draft": draft}


def resolve_validate(inp: dict) -> dict:
    draft = validate_state(dict(inp["draft"]))
    draft["updatedAt"] = now_iso()
    c = _consult(inp)
    store().update_consultation(c, draft=draft)
    return {**inp, "draft": draft, "blocksApproval": bool(draft.get("blocks_approval"))}


def unblock(inp: dict) -> dict:
    """Gate failed: hand the consult back to the dashboard with the offending draft."""
    c = _consult(inp)
    store().update_consultation(c, status="LIVE", approvalBlockedAt=now_iso(),
                                approvalBlockedBy=inp["draft"].get("approval_blocked_by", []))
    return {**inp, "returnedToDashboard": True}


def render_pdf(inp: dict) -> dict:
    c = _consult(inp)
    doctor = store().get_doctor(c["doctorId"]) or {}
    patient = store().get_patient(c["patientId"]) or {}
    rx_view = {**inp["draft"], "rxId": inp["rxId"], "createdAt": inp["approvedAt"]}
    kwargs = {"ref": default_reference(), "bucket": MEDSCRIBE_BUCKET}
    try:
        out = pdf.publish(rx_view, doctor, patient, **kwargs)
        fmt = "pdf"
    except ImportError:
        # No WeasyPrint layer on this deployment yet: ship the HTML so the chain still completes.
        log.warning("WeasyPrint unavailable; publishing HTML instead of PDF")
        out = pdf.publish(rx_view, doctor, patient, to_pdf=lambda h: h.encode("utf-8"), **kwargs)
        s3 = boto3.client("s3")
        bucket, html_key = out["bucket"], out["key"][:-4] + ".html"
        s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": out["key"]},
                       Key=html_key, ContentType="text/html; charset=utf-8",
                       MetadataDirective="REPLACE")
        s3.delete_object(Bucket=bucket, Key=out["key"])
        out["key"] = html_key
        out["url"] = s3.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": html_key}, ExpiresIn=out["expires_in"])
        fmt = "html"
    document = {"bucket": out["bucket"], "key": out["key"], "url": out["url"], "format": fmt,
                "unverified": out["unverified"]}
    return {**inp, "document": document}


def _audit(draft: dict) -> dict:
    meds = draft.get("medicines", [])
    return {
        "lockedFields": draft.get("locked_fields", []),
        "doctorEdited": [m["med_key"] for m in meds if m.get("locked") and not m.get("deleted")],
        "doctorDeleted": [m["med_key"] for m in meds if m.get("deleted")],
        "confirmedByDoctor": [m["med_key"] for m in meds
                              if m.get("reason") in ("confirmed by doctor", "set by doctor")],
        "flagsDismissed": [{"med_key": m["med_key"], "clinical": m.get("clinical"),
                            "reason": m.get("clinical_reason")}
                           for m in meds
                           if m.get("clinical") == "CONTRADICTS" and not m.get("deleted")],
        "unverified": [m["med_key"] for m in meds
                       if not m.get("deleted") and not m.get("brand_id")],
    }


def persist_rx(inp: dict) -> dict:
    c = _consult(inp)
    draft = inp["draft"]
    meds = [m for m in draft.get("medicines", []) if not m.get("deleted")]
    if not meds:
        raise PipelineError("no medicines to prescribe")
    # A retry must not erase what later steps already recorded (sentAt etc.).
    existing = store().get_prescription(c["patientId"], inp["approvedAt"], inp["rxId"]) or {}
    rx = store().put_prescription(c["patientId"], c["doctorId"], {
        **existing,
        "rxId": inp["rxId"], "createdAt": inp["approvedAt"], "consultId": c["consultId"],
        "symptoms": draft.get("symptoms", []), "diagnosis": draft.get("diagnosis"),
        "conditions_matched": draft.get("conditions_matched", []),
        "tests_advised": draft.get("tests_advised", []),
        "medicines": [{
            "brand_id": m.get("brand_id"), "label": m.get("matched") or m.get("spoken"),
            "salt_ids": m.get("salt_ids", []), "spoken": m.get("spoken"),
            "frequency": m.get("frequency"), "food_relation": m.get("food_relation"),
            "duration": m.get("duration"), "unverified": m.get("brand_id") is None,
        } for m in meds],
        "next_visit": draft.get("next_visit"), "status": "APPROVED",
        "approvedAt": inp["approvedAt"], "document": inp.get("document"), "audit": _audit(draft),
    })
    if not existing:
        for m in meds:
            if m.get("brand_id"):
                store().bump_shortlist(c["doctorId"], m["brand_id"])
    store().update_consultation(c, status="APPROVED", rxId=rx["rxId"],
                                approvedAt=inp["approvedAt"], document=inp.get("document"))
    return {**inp, "persisted": True}


def _rx(inp: dict) -> dict:
    c = _consult(inp)
    rx = store().get_prescription(c["patientId"], inp["approvedAt"], inp["rxId"])
    if not rx:
        raise PipelineError("prescription not persisted yet")
    return rx


def send_prescription(inp: dict) -> dict:
    rx = _rx(inp)
    if rx.get("sentAt"):
        return {**inp, "prescriptionSend": {"skipped": "already sent", "at": rx["sentAt"]}}
    patient = store().get_patient(rx["patientId"]) or {}
    doctor = store().get_doctor(rx["doctorId"]) or {}
    to = patient.get("phone")
    if not to:
        return {**inp, "prescriptionSend": {"skipped": "patient has no phone"}}
    doc = inp.get("document") or rx.get("document") or {}
    results = []
    if doc.get("url"):
        ext = "pdf" if doc.get("format") == "pdf" else "html"
        caption = f"Prescription from {doctor.get('name', 'your doctor')}"
        filename = f"prescription-{rx['rxId']}.{ext}"
        results.append(messaging.send_document(to, doc["url"], filename, caption=caption))
    results.append(messaging.send_text(to, messaging.prescription_text(rx, doctor)))
    at = now_iso()
    store().put_prescription(rx["patientId"], rx["doctorId"],
                             {**rx, "sentAt": at, "sendResults": results})
    return {**inp, "prescriptionSend": {"at": at, "results": results}}


def same_salt_alternatives(rx: dict, ref=None, limit: int = 4) -> dict[str, list[str]]:
    ref = ref or default_reference()
    out: dict[str, list[str]] = {}
    for m in rx.get("medicines", []):
        if not m.get("brand_id"):
            continue
        salts = set(m.get("salt_ids") or [])
        alts = [brand_label(b) for bid, b in ref.brands.items()
                if bid != m["brand_id"] and set(b.get("salt_ids") or []) == salts]
        out[m["label"]] = sorted(set(alts))[:limit]
    return out


def send_generics(inp: dict) -> dict:
    rx = _rx(inp)
    if rx.get("genericsSentAt"):
        return {**inp, "genericsSend": {"skipped": "already sent"}}
    patient = store().get_patient(rx["patientId"]) or {}
    text = messaging.generics_text(same_salt_alternatives(rx))
    if not text or not patient.get("phone"):
        return {**inp, "genericsSend": {"skipped": "nothing to send"}}
    result = messaging.send_text(patient["phone"], text)
    at = now_iso()
    store().put_prescription(rx["patientId"], rx["doctorId"], {**rx, "genericsSentAt": at})
    return {**inp, "genericsSend": {"at": at, "result": result}}


SLOT_HOURS = {"1-0-0": [9], "0-1-0": [14], "0-0-1": [21], "1-0-1": [9, 21], "1-1-1": [9, 14, 21],
              "1-1-0": [9, 14], "0-1-1": [14, 21], "OD": [9], "BD": [9, 21], "TDS": [9, 14, 21],
              "QID": [8, 12, 16, 21], "HS": [21]}


def _days(duration: str | None) -> int:
    if not duration:
        return 5
    num = "".join(ch for ch in duration if ch.isdigit())
    n = int(num) if num else 5
    if "week" in duration or "hafte" in duration:
        n *= 7
    if "month" in duration or "mahine" in duration:
        n *= 30
    return max(1, min(n, 90))


def reminder_plan(rx: dict, start: datetime) -> list[dict]:
    """Deterministic reminder ids: rem-<rxId>-<brandId|idx>-<YYYYMMDD>-<HH>. Times in IST."""
    plan = []
    for i, m in enumerate(rx.get("medicines", [])):
        hours = SLOT_HOURS.get(str(m.get("frequency") or ""))
        if not hours:
            continue
        key = m.get("brand_id") or f"m{i}"
        shift = {"after food": 0.5, "before food": -0.5}.get(m.get("food_relation") or "", 0)
        for d in range(_days(m.get("duration"))):
            day = start + timedelta(days=d)
            for h in hours:
                midnight = day.replace(hour=0, minute=0, second=0, microsecond=0)
                due = midnight + timedelta(hours=h + shift)
                if due <= start:
                    continue
                plan.append({
                    "remId": f"rem-{rx['rxId']}-{key}-{due.strftime('%Y%m%d-%H%M')}",
                    "dueAt": due.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "brand_id": m.get("brand_id"), "label": m.get("label"),
                    "text": (f"Time for {m.get('label')}: {messaging.plain_timing(m)}. "
                             "Reply TAKEN once you've had it."),
                    "rxId": rx["rxId"],
                })
    return plan


def schedule_reminders(inp: dict) -> dict:
    rx = _rx(inp)
    if rx.get("remindersScheduledAt"):
        return {**inp, "reminders": {"skipped": "already scheduled",
                                     "count": rx.get("reminderCount", 0)}}
    ist = timezone(timedelta(hours=5, minutes=30))
    start = datetime.now(ist)
    plan = reminder_plan(rx, start)
    for r in plan:
        store().put_reminder(rx["patientId"], r)   # deterministic remId -> idempotent
    # EventBridge Scheduler wiring is step 12; rows are the source of truth it will read.
    at = now_iso()
    store().put_prescription(rx["patientId"], rx["doctorId"],
                             {**rx, "remindersScheduledAt": at, "reminderCount": len(plan)})
    return {**inp, "reminders": {"count": len(plan), "at": at}}


def mark_failed(inp: dict) -> dict:
    c = store().get_consult_by_id(inp.get("consultId", ""))
    err = inp.get("error") or {}
    if c:
        store().update_consultation(c, status="APPROVAL_FAILED", approvalError=str(err)[:500],
                                    approvalFailedAt=now_iso())
    return {**inp, "markedFailed": True}


STEPS = {
    "final_transcript": final_transcript, "final_extract": final_extract,
    "resolve_validate": resolve_validate, "unblock": unblock, "render_pdf": render_pdf,
    "persist_rx": persist_rx, "send_prescription": send_prescription,
    "send_generics": send_generics, "schedule_reminders": schedule_reminders,
    "mark_failed": mark_failed,
}


def handler(event, context):
    step = event.get("step")
    if step not in STEPS:
        raise PipelineError(f"unknown step {step!r}")
    inp = dict(event.get("input") or {})
    log.info("step=%s consult=%s rx=%s", step, inp.get("consultId"), inp.get("rxId"))
    out = STEPS[step](inp)
    if step == "final_extract":
        out.pop("transcriptText", None)  # keep the state payload small past this point
    return out
