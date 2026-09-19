"""Prescription rendering: HTML layout rules and the S3 publish path (moto).

WeasyPrint needs native libs that are not on a Windows checkout, so the
HTML -> PDF step is injected; `test_render_pdf_bytes` runs only where it imports.
"""
import os
import re

import boto3
import pytest
from moto import mock_aws

from core import pdf
from core.reference import default_reference
from scripts.seed_demo_data import DOCTOR, PATIENTS

REF = default_reference()
PATIENT, VISITS = PATIENTS[0]
RX = {**VISITS[2][1], "rxId": "rx-test-001", "createdAt": "2026-09-19T10:30:00Z"}


def html_for(rx=RX, patient=PATIENT, doctor=DOCTOR):
    return pdf.render_html(pdf.build_view(rx, doctor, patient, REF))


def text_of(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def test_header_and_patient_block():
    t = text_of(html_for())
    for needle in (DOCTOR["name"], DOCTOR["degree"], DOCTOR["registrationNumber"], DOCTOR["clinic"],
                   PATIENT["name"], PATIENT["patientId"], "2026-09-19"):
        assert needle in t, needle


def test_salt_first_in_capitals_brand_in_brackets_form_and_dose():
    html = html_for()
    assert "PARACETAMOL 650 mg" in html
    assert "(Dolo 650mg)" in html
    assert "Tablet" in html
    assert "1-0-1 · after food · 5 days" in html
    # salt line precedes the brand on the same medicine
    assert html.index("PARACETAMOL 650 mg") < html.index("(Dolo 650mg)")


def test_combination_drug_lists_every_salt():
    sinarest = {"brand_id": "B040", "label": "Sinarest 500mg", "salt_ids": ["S001", "S009", "S011"],
                "frequency": "1-1-1", "food_relation": "after food", "duration": "5 days"}
    rx = {**RX, "medicines": [sinarest]}
    html = html_for(rx)
    assert "PARACETAMOL 500 mg + CHLORPHENIRAMINE MALEATE + PHENYLEPHRINE" in html


def test_no_price_anywhere():
    t = text_of(html_for()).lower()
    for banned in ("₹", "rs.", "price", "mrp", "cost"):
        assert banned not in t, banned


def test_clinical_body_sections_and_empty_states():
    t = text_of(html_for())
    assert "fever" in t and "viral fever" in t.lower() and "CBC" in t and "5 days" in t
    empty = {**RX, "symptoms": [], "diagnosis": None, "tests_advised": [], "next_visit": None}
    t2 = text_of(html_for(empty))
    assert "Not recorded" in t2 and "Tests advised" not in t2 and "Follow-up" not in t2


def test_unverified_medicine_prints_normally_but_is_flagged_internally():
    rx = {**RX, "medicines": [
        {"brand_id": "B001", "label": "Dolo 650mg", "salt_ids": ["S001"], "frequency": "1-0-1"},
        {"brand_id": None, "spoken": "zorblaxitron 900", "matched": None, "salt_ids": [],
         "med_key": "zorblaxitron 900", "frequency": "0-0-1"},
    ]}
    view = pdf.build_view(rx, DOCTOR, PATIENT, REF)
    assert view["unverified"] == ["zorblaxitron 900"]
    html = pdf.render_html(view)
    assert "ZORBLAXITRON 900" in html and "(as prescribed)" in html
    assert "unverified" not in html.lower()


def test_deleted_medicines_are_not_printed():
    gone = {"brand_id": "B001", "label": "Dolo 650mg", "salt_ids": ["S001"], "deleted": True}
    rx = {**RX, "medicines": [gone]}
    assert "PARACETAMOL" not in html_for(rx)


def test_publish_uploads_pdf_and_returns_presigned_url():
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-south-1")
        s3.create_bucket(Bucket="medscribe-test",
                         CreateBucketConfiguration={"LocationConstraint": "ap-south-1"})
        out = pdf.publish(RX, DOCTOR, PATIENT, ref=REF, s3=s3, bucket="medscribe-test",
                          to_pdf=lambda html: b"%PDF-1.4 fake " + html[:20].encode())

        assert out["key"] == "rx/rx-test-001.pdf" and out["expires_in"] == 24 * 3600
        assert out["url"].startswith("https://") and "rx/rx-test-001.pdf" in out["url"]
        assert "X-Amz-Signature" in out["url"] or "Signature=" in out["url"]
        obj = s3.get_object(Bucket="medscribe-test", Key=out["key"])
        assert obj["ContentType"] == "application/pdf" and obj["Body"].read().startswith(b"%PDF")
        assert out["unverified"] == []


def test_publish_requires_bucket():
    with pytest.raises(ValueError):
        pdf.publish(RX, DOCTOR, PATIENT, ref=REF, s3=object(), bucket="", to_pdf=lambda h: b"x")


def test_render_pdf_bytes():
    pytest.importorskip("weasyprint")
    out = pdf.render_pdf(html_for())
    assert out.startswith(b"%PDF") and len(out) > 5000
