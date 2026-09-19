"""Consultation API (docs/API_CONTRACT.md) as one HTTP API Lambda.

Extraction is debounced server-side: an append only marks the consult dirty;
the next GET /draft (the dashboard polls every 3 s) runs process() if
EXTRACTION_INTERVAL_SECONDS have passed since the last pass. No queues, no
async invokes, and a poll never sees a half-written draft.
"""
import json
import logging
import os
import time

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)


def _load_secrets_from_ssm() -> None:
    """Populate os.environ from /medscribe/<env>/* before core.settings is imported."""
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
    except Exception as e:  # noqa: BLE001 - a missing secret surfaces as an LLM error later
        log.warning("could not load SSM secrets: %s", e)


_load_secrets_from_ssm()

from core import history_chat, ws  # noqa: E402
from core.draft_edit import PatchError, apply_patch  # noqa: E402
from core.llm import get_client  # noqa: E402
from core.pipeline import empty_draft, process  # noqa: E402
from core.reference import default_reference  # noqa: E402
from core.settings import EXTRACTION_INTERVAL_SECONDS, STATE_MACHINE_ARN  # noqa: E402
from core.store import Store, new_id, now_iso  # noqa: E402

ALLOW_HEADER_AUTH = os.environ.get("ALLOW_HEADER_AUTH", "false").lower() == "true"

_store = None
_client = None
_sfn = None


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


def sfn():
    global _sfn
    if _sfn is None:
        _sfn = boto3.client("stepfunctions")
    return _sfn


# -- http plumbing -----------------------------------------------------------

class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def respond(status: int, body) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def claims_of(event: dict) -> dict:
    authorizer = event.get("requestContext", {}).get("authorizer") or {}
    return (authorizer.get("jwt") or {}).get("claims") or {}


def is_patient_token(claims: dict) -> bool:
    return "phone_number" in claims and "custom:doctorId" not in claims


def doctor_id_from(event: dict) -> str:
    claims = claims_of(event)
    if claims and not is_patient_token(claims):
        return claims.get("custom:doctorId") or claims["sub"]
    if ALLOW_HEADER_AUTH and not claims:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if headers.get("x-doctor-id"):
            return headers["x-doctor-id"]
    raise HttpError(401, "unauthorised")


def patient_from(event: dict) -> dict:
    """The caller's own patient record, resolved from the patient-pool token's phone.

    Identity never comes from the URL or body: a patient can only ever reach
    their own records, whatever ids they send.
    """
    claims = claims_of(event)
    if not claims or not is_patient_token(claims):
        raise HttpError(401, "patient login required")
    patient = store().get_patient_by_phone(claims["phone_number"])
    if not patient:
        raise HttpError(404, "no patient record for this number")
    return patient


def is_reviewer(event: dict) -> bool:
    groups = claims_of(event).get("cognito:groups") or []
    if isinstance(groups, str):
        groups = [g.strip() for g in groups.strip("[]").split(",") if g.strip()]
    return "reviewers" in groups


def body_of(event: dict) -> dict:
    raw = event.get("body") or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HttpError(400, f"invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise HttpError(400, "body must be a JSON object")
    return data


def owned_consult(consult_id: str, doctor_id: str) -> dict:
    c = store().get_consult_by_id(consult_id)
    if not c or c.get("doctorId") != doctor_id:
        raise HttpError(404, "consult not found")
    return c


# -- routes -------------------------------------------------------------------

def list_patients(doctor_id: str) -> list[dict]:
    out = []
    for p in store().list_patients_for_doctor(doctor_id):
        rx = store().list_prescriptions(p["patientId"], limit=1)
        out.append({
            "patientId": p["patientId"], "name": p.get("name"), "age": p.get("age"),
            "sex": p.get("sex"), "phone": p.get("phone"),
            "lastVisit": rx[0]["createdAt"][:10] if rx else None,
        })
    return out


def public_consult(c: dict) -> dict:
    return {
        "consultId": c["consultId"], "patientId": c["patientId"], "doctorId": c["doctorId"],
        "status": c.get("status", "LIVE"), "createdAt": c["createdAt"],
        "transcript": c.get("transcript", []), "draft": c.get("draft") or empty_draft(),
        "rxId": c.get("rxId"), "approvalError": c.get("approvalError"),
        "approvalBlockedBy": c.get("approvalBlockedBy"),
    }


def create_consult(doctor_id: str, body: dict) -> dict:
    patient_id = body.get("patientId")
    patient = store().get_patient(patient_id) if patient_id else None
    if not patient or patient.get("doctorId") != doctor_id:
        raise HttpError(404, "patient not found")
    c = store().create_consult(patient_id, doctor_id, {
        "status": "LIVE", "transcript": [], "draft": {**empty_draft(), "updatedAt": now_iso()},
        "lastExtractedAt": 0, "dirty": False,
    })
    return public_consult(c)


def append_transcript(c: dict, body: dict) -> dict:
    text = (body.get("text") or "").strip()
    if not text:
        raise HttpError(400, "text is required")
    if c.get("status") != "LIVE":
        raise HttpError(409, "consult is not live")
    lines = list(c.get("transcript", []))
    seq = body.get("seq") or (lines[-1]["seq"] + 1 if lines else 1)
    lines.append({"seq": seq, "speaker": body.get("speaker"), "text": text, "at": now_iso()})
    store().update_consultation(c, transcript=lines, dirty=True)
    return {"accepted": True, "seq": seq}


SPEAKER_LABEL = {"doctor": "Doctor", "patient": "Patient"}


def transcript_text(c: dict) -> str:
    lines = []
    for line in c.get("transcript", []):
        label = SPEAKER_LABEL.get(line.get("speaker"))
        lines.append(f"{label}: {line['text']}" if label else line["text"])
    return "\n".join(lines)


def get_draft(c: dict) -> dict:
    since_last = time.time() - float(c.get("lastExtractedAt") or 0)
    due = c.get("dirty") and since_last >= EXTRACTION_INTERVAL_SECONDS
    if due and c.get("status") == "LIVE" and c.get("transcript"):
        draft = process(transcript_text(c), c.get("draft"), client=llm())
        draft["updatedAt"] = now_iso()
        c = store().update_consultation(c, draft=draft, dirty=False, lastExtractedAt=time.time())
    return c.get("draft") or empty_draft()


def patch_draft(c: dict, body: dict) -> dict:
    if c.get("status") != "LIVE":
        raise HttpError(409, "consult is not live")
    try:
        draft = apply_patch(c.get("draft") or empty_draft(), body, default_reference())
    except PatchError as e:
        raise HttpError(400, str(e)) from e
    draft["updatedAt"] = now_iso()
    store().update_consultation(c, draft=draft)
    ws.broadcast(store(), c["consultId"], ws.draft_update(draft))   # other tabs / sockets
    return draft


def ws_ticket(c: dict) -> dict:
    if c.get("status") != "LIVE":
        raise HttpError(409, "consult is not live")
    return {"ticket": store().put_ws_ticket(c["consultId"], c["doctorId"]), "expires_in": 60}


def approve(c: dict) -> dict:
    """Start the post-approval pipeline (spec §3). Without a state machine (tests,
    local), persist inline so the API still completes the story end to end."""
    if c.get("status") == "APPROVED":
        return {"status": "APPROVED", "rxId": c.get("rxId")}
    if c.get("status") == "APPROVING":
        return {"status": "APPROVING", "rxId": c.get("rxId"), "executionArn": c.get("executionArn")}
    draft = c.get("draft") or empty_draft()
    if draft.get("blocks_approval"):
        raise HttpError(409, "unresolved medicines block approval")
    meds = [m for m in draft.get("medicines", []) if not m.get("deleted")]
    if not meds:
        raise HttpError(409, "no medicines to prescribe")

    rx_id, approved_at = new_id(), now_iso()
    if STATE_MACHINE_ARN:
        run = sfn().start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=f"approve-{c['consultId']}-{rx_id}",
            input=json.dumps({"consultId": c["consultId"], "patientId": c["patientId"],
                              "doctorId": c["doctorId"], "rxId": rx_id, "approvedAt": approved_at}),
        )
        store().update_consultation(c, status="APPROVING", rxId=rx_id,
                                    executionArn=run["executionArn"])
        return {"status": "APPROVING", "rxId": rx_id, "executionArn": run["executionArn"]}

    rx = store().put_prescription(c["patientId"], c["doctorId"], {
        "rxId": rx_id, "createdAt": approved_at, "consultId": c["consultId"],
        "symptoms": draft.get("symptoms", []),
        "diagnosis": draft.get("diagnosis"),
        "conditions_matched": draft.get("conditions_matched", []),
        "tests_advised": draft.get("tests_advised", []),
        "medicines": [{
            "brand_id": m.get("brand_id"), "label": m.get("matched") or m.get("spoken"),
            "salt_ids": m.get("salt_ids", []), "spoken": m.get("spoken"),
            "frequency": m.get("frequency"), "food_relation": m.get("food_relation"),
            "duration": m.get("duration"), "unverified": m.get("brand_id") is None,
        } for m in meds],
        "next_visit": draft.get("next_visit"),
        "status": "APPROVED",
        "approvedAt": approved_at,
    })
    for m in meds:
        if m.get("brand_id"):
            store().bump_shortlist(c["doctorId"], m["brand_id"])
    store().update_consultation(c, status="APPROVED", rxId=rx["rxId"], approvedAt=rx["approvedAt"])
    return {"status": "APPROVED", "rxId": rx["rxId"]}


def prescription(c: dict) -> dict:
    """The approved prescription plus a fresh presigned link to its document."""
    if c.get("status") != "APPROVED" or not c.get("rxId"):
        raise HttpError(404, "consult has no approved prescription yet")
    rx = store().get_prescription(c["patientId"], c["approvedAt"], c["rxId"])
    if not rx:
        raise HttpError(404, "prescription not found")
    doc = rx.get("document") or c.get("document") or {}
    out = {"rxId": rx["rxId"], "approvedAt": rx.get("approvedAt"), "diagnosis": rx.get("diagnosis"),
           "medicines": rx.get("medicines", []), "sentAt": rx.get("sentAt"),
           "document": None}
    if doc.get("key"):
        url = boto3.client("s3").generate_presigned_url(
            "get_object", Params={"Bucket": doc["bucket"], "Key": doc["key"]}, ExpiresIn=3600)
        out["document"] = {"format": doc.get("format"), "url": url, "expires_in": 3600}
    return out


def document_link(doc: dict | None, ttl: int = 3600) -> dict | None:
    if not doc or not doc.get("key"):
        return None
    url = boto3.client("s3").generate_presigned_url(
        "get_object", Params={"Bucket": doc["bucket"], "Key": doc["key"]}, ExpiresIn=ttl)
    return {"format": doc.get("format"), "url": url, "expires_in": ttl}


def patient_history(patient: dict) -> dict:
    rxs = store().list_prescriptions(patient["patientId"])
    doctors = {}
    for rx in rxs:
        did = rx.get("doctorId")
        if did and did not in doctors:
            doctors[did] = store().get_doctor(did) or {}
    return {
        "patient": {k: patient.get(k) for k in ("patientId", "name", "age", "sex", "phone",
                                                 "chronicConditions", "allergies")},
        "prescriptions": [{
            "rxId": rx["rxId"], "date": (rx.get("approvedAt") or rx.get("createdAt") or "")[:10],
            "doctor": (doctors.get(rx.get("doctorId")) or {}).get("name"),
            "clinic": (doctors.get(rx.get("doctorId")) or {}).get("clinic"),
            "diagnosis": rx.get("diagnosis"), "symptoms": rx.get("symptoms") or [],
            "tests_advised": rx.get("tests_advised") or [], "next_visit": rx.get("next_visit"),
            "medicines": rx.get("medicines") or [],
            "document": document_link(rx.get("document")),
        } for rx in rxs],
        "reminders": [{
            "remId": r["remId"], "dueAt": r["dueAt"], "label": r.get("label"),
            "status": r.get("status"), "takenAt": r.get("takenAt"),
        } for r in store().list_reminders(patient["patientId"])[-60:]],
    }


def ask_history(patient: dict, body: dict) -> dict:
    question = (body.get("question") or "").strip()
    if not question:
        raise HttpError(400, "question is required")
    if len(question) > 500:
        raise HttpError(400, "question too long")
    rxs = store().list_prescriptions(patient["patientId"])
    doctors = {rx["doctorId"]: store().get_doctor(rx["doctorId"]) or {}
               for rx in rxs if rx.get("doctorId")}
    context = history_chat.build_context(patient, rxs, doctors)
    return history_chat.ask(question, context, client=llm())


def owned_patient(patient_id: str, doctor_id: str) -> dict:
    patient = store().get_patient(patient_id)
    if not patient or patient.get("doctorId") != doctor_id:
        raise HttpError(404, "patient not found")
    return patient


def list_pending(event: dict) -> list[dict]:
    if not is_reviewer(event):
        raise HttpError(403, "reviewer role required")
    return store().list_pending_proposals()


def review(event: dict, created_at: str, prop_id: str, body: dict) -> dict:
    if not is_reviewer(event):
        raise HttpError(403, "reviewer role required")
    decision = (body.get("decision") or "").upper()
    if decision not in ("APPROVED", "REJECTED"):
        raise HttpError(400, "decision must be APPROVED or REJECTED")
    reviewer = claims_of(event).get("email") or claims_of(event).get("sub", "reviewer")
    try:
        store().review_proposal(created_at, prop_id, decision, reviewer=reviewer)
    except KeyError as e:
        raise HttpError(404, str(e)) from e
    return {"status": decision, "propId": prop_id}


def search_brands(event: dict) -> list[dict]:
    q = (event.get("queryStringParameters") or {}).get("q", "")
    return default_reference().search_brands(q)


# -- router -------------------------------------------------------------------

def route(event: dict) -> dict:
    method = event["requestContext"]["http"]["method"].upper()
    path = event.get("rawPath") or event["requestContext"]["http"]["path"]
    stage = event.get("requestContext", {}).get("stage")
    if stage and path.startswith(f"/{stage}/"):
        path = path[len(stage) + 1:]
    parts = [p for p in path.split("/") if p]

    if parts and parts[0] == "me":
        patient = patient_from(event)
        if len(parts) == 1 and method == "GET":
            return respond(200, patient_history(patient)["patient"])
        if parts[1:] == ["history"] and method == "GET":
            return respond(200, patient_history(patient))
        if parts[1:] == ["ask"] and method == "POST":
            return respond(200, ask_history(patient, body_of(event)))
        raise HttpError(404, f"no route for {method} {path}")

    doctor_id = doctor_id_from(event)

    if parts[:1] == ["review"]:
        if parts == ["review", "pending"] and method == "GET":
            return respond(200, list_pending(event))
        if len(parts) == 3 and method == "POST":
            return respond(200, review(event, parts[1], parts[2], body_of(event)))
    if len(parts) == 3 and parts[0] == "patients":
        patient = owned_patient(parts[1], doctor_id)
        if parts[2] == "history" and method == "GET":
            return respond(200, patient_history(patient))
        if parts[2] == "ask" and method == "POST":
            return respond(200, ask_history(patient, body_of(event)))
    if parts == ["patients"] and method == "GET":
        return respond(200, list_patients(doctor_id))
    if parts == ["brands"] and method == "GET":
        return respond(200, search_brands(event))
    if parts == ["consults"] and method == "POST":
        return respond(201, create_consult(doctor_id, body_of(event)))
    if len(parts) >= 2 and parts[0] == "consults":
        c = owned_consult(parts[1], doctor_id)
        sub = parts[2] if len(parts) > 2 else None
        if sub is None and method == "GET":
            return respond(200, public_consult(c))
        if sub == "transcript" and method == "POST":
            return respond(200, append_transcript(c, body_of(event)))
        if sub == "draft" and method == "GET":
            return respond(200, get_draft(c))
        if sub == "draft" and method == "PATCH":
            return respond(200, patch_draft(c, body_of(event)))
        if sub == "approve" and method == "POST":
            out = approve(c)
            return respond(202 if out["status"] == "APPROVING" else 200, out)
        if sub == "prescription" and method == "GET":
            return respond(200, prescription(c))
        if sub == "ws-ticket" and method == "POST":
            return respond(200, ws_ticket(c))
    raise HttpError(404, f"no route for {method} {path}")


def handler(event, context):
    try:
        return route(event)
    except HttpError as e:
        return respond(e.status, {"error": str(e)})
    except Exception as e:  # noqa: BLE001 - never leak a traceback to the client
        log.exception("unhandled error")
        return respond(500, {"error": f"internal error: {type(e).__name__}"})
