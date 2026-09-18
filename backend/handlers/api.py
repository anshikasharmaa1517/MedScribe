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

from core.draft_edit import PatchError, apply_patch  # noqa: E402
from core.llm import get_client  # noqa: E402
from core.pipeline import empty_draft, process  # noqa: E402
from core.reference import default_reference  # noqa: E402
from core.settings import EXTRACTION_INTERVAL_SECONDS  # noqa: E402
from core.store import Store, now_iso  # noqa: E402

ALLOW_HEADER_AUTH = os.environ.get("ALLOW_HEADER_AUTH", "false").lower() == "true"

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


def doctor_id_from(event: dict) -> str:
    authorizer = event.get("requestContext", {}).get("authorizer") or {}
    claims = (authorizer.get("jwt") or {}).get("claims") or {}
    if claims:
        return claims.get("custom:doctorId") or claims["sub"]
    if ALLOW_HEADER_AUTH:
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if headers.get("x-doctor-id"):
            return headers["x-doctor-id"]
    raise HttpError(401, "unauthorised")


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
    return draft


def approve(c: dict) -> dict:
    if c.get("status") == "APPROVED":
        return {"status": "APPROVED", "rxId": c.get("rxId")}
    draft = c.get("draft") or empty_draft()
    if draft.get("blocks_approval"):
        raise HttpError(409, "unresolved medicines block approval")
    meds = [m for m in draft.get("medicines", []) if not m.get("deleted")]
    if not meds:
        raise HttpError(409, "no medicines to prescribe")
    rx = store().put_prescription(c["patientId"], c["doctorId"], {
        "consultId": c["consultId"],
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
        "approvedAt": now_iso(),
    })
    for m in meds:
        if m.get("brand_id"):
            store().bump_shortlist(c["doctorId"], m["brand_id"])
    store().update_consultation(c, status="APPROVED", rxId=rx["rxId"], approvedAt=rx["approvedAt"])
    return {"status": "APPROVED", "rxId": rx["rxId"]}


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
    doctor_id = doctor_id_from(event)

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
            return respond(200, approve(c))
    raise HttpError(404, f"no route for {method} {path}")


def handler(event, context):
    try:
        return route(event)
    except HttpError as e:
        return respond(e.status, {"error": str(e)})
    except Exception as e:  # noqa: BLE001 - never leak a traceback to the client
        log.exception("unhandled error")
        return respond(500, {"error": f"internal error: {type(e).__name__}"})
