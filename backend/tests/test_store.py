"""Store tests run against moto's in-memory DynamoDB - never real AWS."""
import os

import pytest
from moto import mock_aws

from core import store as store_mod
from core.settings import BRANDS_TABLE, CONDITIONS_TABLE, SALTS_TABLE
from core.store import Store, create_tables, from_dynamo, to_dynamo
from core.validator import Validator
from scripts import seed_demo_data, seed_reference_tables


@pytest.fixture
def store():
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        db = store_mod.dynamodb_resource(endpoint_url=None, region="ap-south-1")
        create_tables(db)
        yield Store(db)


def test_create_tables_is_idempotent(store):
    create_tables(store.db)
    assert {t.name for t in store.db.tables.all()} >= {
        "medscribe", BRANDS_TABLE, SALTS_TABLE, CONDITIONS_TABLE
    }


def test_doctor_round_trip(store):
    d = store.put_doctor({"name": "Dr. X", "degree": "MBBS"})
    got = store.get_doctor(d["doctorId"])
    assert got["name"] == "Dr. X" and got["entity"] == "DOCTOR"
    assert store.get_doctor("nope") is None


def test_patient_phone_lookup_and_doctor_list(store):
    p1 = store.put_patient({"name": "A", "phone": "+919800000001", "doctorId": "doc1"})
    store.put_patient({"name": "B", "phone": "+919800000002", "doctorId": "doc1"})
    store.put_patient({"name": "C", "phone": "+919800000003", "doctorId": "doc2"})

    assert store.get_patient_by_phone("+919800000001")["patientId"] == p1["patientId"]
    assert store.get_patient_by_phone("+910000000000") is None
    names = sorted(p["name"] for p in store.list_patients_for_doctor("doc1"))
    assert names == ["A", "B"]


def test_consultations_order_and_todays_list(store):
    pid = "p1"
    store.put_consultation(pid, "doc1", {"createdAt": "2026-09-18T09:00:00Z", "status": "DONE"})
    c2 = store.put_consultation(pid, "doc1", {"createdAt": "2026-09-19T10:00:00Z", "status": "ON"})
    store.put_consultation("p2", "doc1", {"createdAt": "2026-09-19T11:00:00Z", "status": "LIVE"})

    newest = store.list_consultations(pid)
    assert [c["createdAt"] for c in newest] == ["2026-09-19T10:00:00Z", "2026-09-18T09:00:00Z"]
    assert store.get_consultation(pid, c2["createdAt"], c2["consultId"])["status"] == "ON"

    today = store.list_consultations_for_doctor("doc1", day="2026-09-19")
    assert {c["patientId"] for c in today} == {"p1", "p2"}
    assert len(store.list_consultations_for_doctor("doc1")) == 3


def test_prescriptions_newest_first_with_limit_and_float_roundtrip(store):
    pid = "p1"
    for i, day in enumerate(("01", "02", "03")):
        store.put_prescription(pid, "doc1", {
            "createdAt": f"2026-09-{day}T08:00:00Z",
            "medicines": [{"brand_id": "B001", "alternatives": [{"score": 58.63}]}],
            "n": i,
        })
    got = store.list_prescriptions(pid, limit=2)
    assert [r["n"] for r in got] == [2, 1]
    assert got[0]["medicines"][0]["alternatives"][0]["score"] == 58.63
    assert isinstance(got[0]["n"], int)


def test_reminders_and_status_update(store):
    r = store.put_reminder("p1", {"dueAt": "2026-09-20T08:00:00Z", "brand_id": "B001"})
    store.put_reminder("p1", {"dueAt": "2026-09-21T08:00:00Z", "brand_id": "B001"})
    assert len(store.list_reminders("p1")) == 2
    assert len(store.list_reminders("p1", from_iso="2026-09-21T00:00:00Z")) == 1

    store.set_reminder_status("p1", r["dueAt"], r["remId"], "SENT")
    assert store.list_reminders("p1")[0]["status"] == "SENT"


def test_shortlist_bump_list_remove(store):
    assert store.bump_shortlist("doc1", "B001") == 1
    assert store.bump_shortlist("doc1", "B001") == 2
    store.add_to_shortlist("doc1", "B025", note="allergy go-to")
    items = {s["brandId"]: s for s in store.list_shortlist("doc1")}
    assert items["B001"]["useCount"] == 2 and items["B025"]["note"] == "allergy go-to"

    store.remove_from_shortlist("doc1", "B025")
    assert [s["brandId"] for s in store.list_shortlist("doc1")] == ["B001"]


def test_review_queue_never_touches_reference_tables(store):
    before = store.count_reference(store.brands)
    p = store.enqueue_proposal({"proposed_brand": "Zorblax 900", "source": "web"})
    assert [x["propId"] for x in store.list_pending_proposals()] == [p["propId"]]

    store.review_proposal(p["createdAt"], p["propId"], "APPROVED", reviewer="admin")
    assert store.list_pending_proposals() == []
    assert store.count_reference(store.brands) == before
    assert store.get_brand("Zorblax 900") is None

    with pytest.raises(ValueError):
        store.review_proposal(p["createdAt"], p["propId"], "MAYBE", reviewer="admin")
    with pytest.raises(KeyError):                       # already decided
        store.review_proposal(p["createdAt"], p["propId"], "REJECTED", reviewer="admin")
    with pytest.raises(KeyError):                       # never existed - no upsert
        store.review_proposal("2026-01-01T00:00:00Z", "ghost", "APPROVED", reviewer="admin")
    assert store._get("QUEUE", "PENDING#2026-01-01T00:00:00Z#ghost") is None


def test_dynamo_conversions():
    assert to_dynamo({"a": 1.5, "b": [2.0, {"c": 3}]}) == {
        "a": store_mod.Decimal("1.5"), "b": [store_mod.Decimal("2.0"), {"c": 3}]
    }
    assert from_dynamo(to_dynamo({"a": 1.5, "b": 2.0, "c": 3})) == {"a": 1.5, "b": 2, "c": 3}


def test_seed_reference_tables_loads_all_rows(store):
    counts = seed_reference_tables.seed(store)
    assert counts == {BRANDS_TABLE: 220, SALTS_TABLE: 120, CONDITIONS_TABLE: 20}

    dolo = store.get_brand("B001")
    assert dolo["base_brand"] == "Dolo" and dolo["salt_ids"] == ["S001"]
    assert "dolo 650" in dolo["aliases"]
    assert store.get_salt("S001")["generic_name"] == "Paracetamol"
    assert "bukhar" in store.get_condition("C001")["synonyms"]


def test_seed_demo_data_creates_doctor_patients_and_history(store):
    out = seed_demo_data.seed(store)

    doc = store.get_doctor(out["doctorId"])
    assert doc["registrationNumber"] and doc["clinic"]
    assert len(out["patients"]) == 3
    assert len(store.list_patients_for_doctor(out["doctorId"])) == 3
    assert store.get_patient_by_phone("+919800000001")["name"] == "Ramesh Iyer"

    total_rx = sum(len(store.list_prescriptions(p)) for p in out["patients"])
    assert total_rx == out["prescriptions"] == 8
    assert all(len(store.list_prescriptions(p)) >= 2 for p in out["patients"])

    shortlist = {s["brandId"]: s["useCount"] for s in store.list_shortlist(out["doctorId"])}
    assert shortlist["B001"] == 2 and shortlist["B130"] == 3


def test_seed_demo_data_is_rerunnable(store):
    seed_demo_data.seed(store)
    seed_demo_data.seed(store)
    assert len(store.list_patients_for_doctor(seed_demo_data.DOCTOR_ID)) == 3
    assert len(store.list_prescriptions("pat-demo-001")) == 3


def test_demo_history_is_clinically_clean(store):
    """The seeded history must pass the real validator, or the demo shows red flags."""
    out = seed_demo_data.seed(store)
    v = Validator()
    for pid in out["patients"]:
        for rx in store.list_prescriptions(pid):
            meds = [{"salt_ids": m["salt_ids"], "status": "AUTO", "matched": m["label"]}
                    for m in rx["medicines"]]
            result = v.validate(meds, rx["diagnosis"])
            assert result["conditions_matched"], rx["diagnosis"]
            verdicts = [m["clinical"] for m in result["medicines"]]
            assert all(v == "OK" for v in verdicts), (rx["diagnosis"], verdicts)
            assert result["duplicate_salts"] == []


def test_reminder_taken_and_counter(store):
    r = store.put_reminder("p1", {"dueAt": "2026-09-20T08:00:00Z", "brand_id": "B001",
                                  "status": "SENT"})
    store.mark_reminder_taken(r)
    got = store.list_reminders("p1")[0]
    assert got["status"] == "TAKEN" and got["takenAt"] and got["takenVia"] == "whatsapp"

    assert store.get_counter("whatsapp_sent") == 0
    assert store.increment_counter("whatsapp_sent") == 1
    assert store.increment_counter("whatsapp_sent") == 2
    assert store.get_counter("whatsapp_sent") == 2


def test_claim_inbound_is_first_writer_wins(store):
    assert store.claim_inbound("wamid.abc") is True
    assert store.claim_inbound("wamid.abc") is False
    assert store.claim_inbound("wamid.xyz") is True
