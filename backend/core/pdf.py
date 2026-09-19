"""Prescription rendering: draft -> HTML -> PDF -> S3 -> 24h presigned URL.

The layout is spec §6: salt first in capitals, brand in brackets, every salt of a
combination drug listed, dosage as `1-0-1 · after food · 5 days`. Prices never
appear here - generics and prices go in a separate WhatsApp message.

WeasyPrint is imported lazily inside `render_pdf` because it needs pango/cairo,
which exist on the Lambda layer and in Docker but not on a bare Windows checkout.
Everything else in this module runs anywhere.
"""
from datetime import UTC, datetime
from pathlib import Path

import boto3
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.reference import Reference, brand_label, default_reference
from core.settings import AWS_REGION, MEDSCRIBE_BUCKET, PDF_URL_TTL_SECONDS

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DOSE_SEPARATOR = " · "

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _salt_line(brand: dict, ref: Reference) -> str:
    """'PARACETAMOL 500 mg + CHLORPHENIRAMINE + PHENYLEPHRINE' for combinations.

    The seed only carries one strength per brand, so it is attached to the first
    salt; further salts are named without a strength rather than given a wrong one.
    """
    parts = []
    for i, sid in enumerate(brand.get("salt_ids") or []):
        salt = ref.get_salt(sid)
        name = (salt["generic_name"] if salt else sid).upper()
        if i == 0 and brand.get("strength"):
            name = f"{name} {brand['strength']} {brand.get('unit', '')}".rstrip()
        parts.append(name)
    return " + ".join(parts)


def _dose_line(med: dict) -> str:
    return DOSE_SEPARATOR.join(
        str(med[k]) for k in ("frequency", "food_relation", "duration") if med.get(k)
    )


def build_medicine_lines(medicines: list[dict], ref: Reference) -> tuple[list[dict], list[str]]:
    """Render-ready lines plus the med_keys/labels that print without a Tier 0 brand."""
    lines, unverified = [], []
    for med in medicines:
        if med.get("deleted"):
            continue
        brand = ref.get_brand(med.get("brand_id")) if med.get("brand_id") else None
        if brand:
            salt_line = _salt_line(brand, ref)
            label = med.get("matched") or med.get("label") or brand_label(brand)
            form = (brand.get("form") or "").capitalize()
        else:
            # Doctor confirmed the name as spoken; no verified salt to lead with.
            label = med.get("matched") or med.get("label") or med.get("spoken") or "Unknown"
            salt_line = label.upper()
            label = "as prescribed"
            form = ""
            unverified.append(med.get("med_key") or salt_line)
        lines.append({
            "salt_line": salt_line,
            "brand_label": label,
            "form": form,
            "dose_line": _dose_line(med),
        })
    return lines, unverified


def build_view(rx: dict, doctor: dict, patient: dict, ref: Reference | None = None) -> dict:
    ref = ref or default_reference()
    medicines, unverified = build_medicine_lines(rx.get("medicines") or [], ref)
    created = rx.get("createdAt") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    age_sex = " / ".join(str(patient[k]) for k in ("age", "sex") if patient.get(k))
    return {
        "doctor": {
            "name": doctor.get("name", ""),
            "degree": doctor.get("degree", ""),
            "registration_number": doctor.get("registrationNumber", ""),
            "clinic": doctor.get("clinic", ""),
            "clinic_address": doctor.get("clinicAddress", ""),
        },
        "patient": {
            "name": patient.get("name", ""),
            "patient_id": patient.get("patientId", ""),
            "age_sex": age_sex,
        },
        "rx": {
            "rx_id": rx.get("rxId", ""),
            "date": created[:10],
            "symptoms": rx.get("symptoms") or [],
            "diagnosis": rx.get("diagnosis"),
            "tests_advised": rx.get("tests_advised") or [],
            "next_visit": rx.get("next_visit"),
        },
        "medicines": medicines,
        "unverified": unverified,
    }


def render_html(view: dict) -> str:
    return _env.get_template("prescription.html").render(**view)


def render_pdf(html: str) -> bytes:
    from weasyprint import HTML  # needs pango/cairo: Lambda layer or Docker

    return HTML(string=html, base_url=str(TEMPLATE_DIR)).write_pdf()


def s3_key(rx_id: str) -> str:
    return f"rx/{rx_id}.pdf"


def publish(rx: dict, doctor: dict, patient: dict, *, ref=None, s3=None,
            bucket=None, ttl_seconds=None, to_pdf=render_pdf) -> dict:
    """Render, upload to s3://<bucket>/rx/<rxId>.pdf, return a presigned GET URL.

    `to_pdf` is injectable so callers without WeasyPrint (tests) can still
    exercise the upload and URL path.
    """
    bucket = bucket or MEDSCRIBE_BUCKET
    if not bucket:
        raise ValueError("MEDSCRIBE_BUCKET is not set")
    s3 = s3 or boto3.client("s3", region_name=AWS_REGION)
    ttl = ttl_seconds or PDF_URL_TTL_SECONDS

    view = build_view(rx, doctor, patient, ref)
    pdf = to_pdf(render_html(view))
    key = s3_key(view["rx"]["rx_id"])
    s3.put_object(
        Bucket=bucket, Key=key, Body=pdf, ContentType="application/pdf",
        Metadata={"unverified": ",".join(view["unverified"])} if view["unverified"] else {},
    )
    url = s3.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl
    )
    return {"bucket": bucket, "key": key, "url": url, "expires_in": ttl,
            "unverified": view["unverified"], "bytes": len(pdf)}
