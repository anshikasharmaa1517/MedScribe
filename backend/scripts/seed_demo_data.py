"""Seed one demo doctor and three synthetic patients with prescription history.

Run from backend/:
    python -m scripts.seed_demo_data                  # app table must exist
    python -m scripts.seed_demo_data --create         # create it first (dev / DynamoDB Local)

IDs are fixed so the dashboard and demo script can reference them. History is
dated relative to now, so "last visit 3 weeks ago" stays true on demo day.
Every brand_id below is a real Tier 0 entry; the diagnoses match what the
validator expects, so the history renders clean rather than flagged.
"""
import argparse
import sys
from datetime import UTC, datetime, timedelta

from core.settings import MEDSCRIBE_TABLE
from core.store import Store, create_tables, dynamodb_resource

DOCTOR_ID = "doc-demo-meera"

DOCTOR = {
    "doctorId": DOCTOR_ID,
    "name": "Dr. Meera Krishnan",
    "degree": "MBBS, MD (General Medicine)",
    "registrationNumber": "KMC 45812",
    "clinic": "Sunrise Family Clinic",
    "clinicAddress": "12th Main, Indiranagar, Bengaluru 560038",
    "phone": "+918040001234",
    "languages": ["en", "hi", "kn"],
}


def rx_med(brand_id, label, salt_ids, frequency, food_relation=None, duration=None):
    return {
        "brand_id": brand_id,
        "label": label,
        "salt_ids": salt_ids,
        "frequency": frequency,
        "food_relation": food_relation,
        "duration": duration,
    }


DOLO = rx_med("B001", "Dolo 650mg", ["S001"], "1-0-1", "after food", "5 days")
CETZINE = rx_med("B025", "Cetzine 10mg", ["S006"], "0-0-1", None, "5 days")
AMLONG = rx_med("B130", "Amlong 5mg", ["S060"], "1-0-0", "after food", "30 days")
GLYCOMET = rx_med("B157", "Glycomet 500mg", ["S074"], "1-0-1", "after food", "30 days")
PAN40 = rx_med("B085", "Pan 40mg", ["S036"], "1-0-0", "before food", "14 days")
MONTEK_LC = rx_med("B039", "Montek LC 10mg", ["S010", "S007"], "0-0-1", None, "15 days")
ALLEGRA = rx_med("B031", "Allegra 120mg", ["S008"], "1-0-0", None, "10 days")
AZITHRAL = rx_med("B058", "Azithral 500mg", ["S019"], "1-0-0", "after food", "3 days")
SINAREST = rx_med(
    "B040", "Sinarest 500mg", ["S001", "S009", "S011"], "1-1-1", "after food", "5 days"
)

# (patient profile, [(days_ago, visit)])
PATIENTS = [
    (
        {
            "patientId": "pat-demo-001",
            "name": "Ramesh Iyer",
            "age": 54,
            "sex": "M",
            "phone": "+919800000001",
            "language": "hi",
            "allergies": [],
            "chronicConditions": ["Hypertension", "Type 2 diabetes"],
        },
        [
            (95, {
                "symptoms": ["headache", "giddiness"],
                "diagnosis": "hypertension, type 2 diabetes",
                "conditions_matched": ["Hypertension", "Type 2 diabetes"],
                "tests_advised": ["HbA1c", "lipid profile"],
                "medicines": [AMLONG, GLYCOMET],
                "next_visit": "1 month",
            }),
            (62, {
                "symptoms": [],
                "diagnosis": "hypertension, type 2 diabetes - follow up",
                "conditions_matched": ["Hypertension", "Type 2 diabetes"],
                "tests_advised": ["fasting blood sugar"],
                "medicines": [AMLONG, GLYCOMET],
                "next_visit": "1 month",
            }),
            (30, {
                "symptoms": ["fever", "body pain"],
                "diagnosis": "viral fever, hypertension, type 2 diabetes",
                "conditions_matched": ["Viral fever", "Hypertension", "Type 2 diabetes"],
                "tests_advised": ["CBC"],
                "medicines": [DOLO, AMLONG, GLYCOMET],
                "next_visit": "5 days",
            }),
        ],
    ),
    (
        {
            "patientId": "pat-demo-002",
            "name": "Priya Sharma",
            "age": 29,
            "sex": "F",
            "phone": "+919800000002",
            "language": "en",
            "allergies": ["dust"],
            "chronicConditions": ["Allergic rhinitis"],
        },
        [
            (120, {
                "symptoms": ["sneezing", "runny nose", "itchy eyes"],
                "diagnosis": "allergic rhinitis",
                "conditions_matched": ["Allergic rhinitis"],
                "tests_advised": [],
                "medicines": [MONTEK_LC, ALLEGRA],
                "next_visit": "2 weeks",
            }),
            (45, {
                "symptoms": ["sore throat", "fever", "cold"],
                "diagnosis": "acute pharyngitis",
                "conditions_matched": ["Acute pharyngitis"],
                "tests_advised": [],
                "medicines": [AZITHRAL, SINAREST],
                "next_visit": "3 days",
            }),
            (14, {
                "symptoms": ["sneezing", "blocked nose"],
                "diagnosis": "allergic rhinitis",
                "conditions_matched": ["Allergic rhinitis"],
                "tests_advised": [],
                "medicines": [MONTEK_LC],
                "next_visit": "1 month",
            }),
        ],
    ),
    (
        {
            "patientId": "pat-demo-003",
            "name": "Arjun Mehta",
            "age": 35,
            "sex": "M",
            "phone": "+919800000003",
            "language": "hi",
            "allergies": [],
            "chronicConditions": ["Acidity / GERD"],
        },
        [
            (80, {
                "symptoms": ["burning in chest", "acidity after meals"],
                "diagnosis": "acidity gerd",
                "conditions_matched": ["Acidity / GERD"],
                "tests_advised": [],
                "medicines": [PAN40],
                "next_visit": "2 weeks",
            }),
            (21, {
                "symptoms": ["fever", "body pain", "cold"],
                "diagnosis": "viral fever",
                "conditions_matched": ["Viral fever"],
                "tests_advised": ["CBC"],
                "medicines": [DOLO, CETZINE],
                "next_visit": "5 days",
            }),
        ],
    ),
]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(store: Store, now: datetime | None = None) -> dict:
    # Visits are pinned to 10:30 UTC so re-running on the same day rewrites the
    # same keys instead of adding a second copy of every prescription.
    anchor = (now or datetime.now(UTC)).replace(hour=10, minute=30, second=0, microsecond=0)
    store.put_doctor(DOCTOR)

    summary = {"doctorId": DOCTOR_ID, "patients": [], "prescriptions": 0}
    shortlist: dict[str, dict] = {}
    for profile, visits in PATIENTS:
        patient = store.put_patient({**profile, "doctorId": DOCTOR_ID})
        for i, (days_ago, visit) in enumerate(visits, start=1):
            at = _iso(anchor - timedelta(days=days_ago))
            consult_id = f"{patient['patientId']}-c{i}"
            rx_id = f"{patient['patientId']}-rx{i}"
            store.put_consultation(patient["patientId"], DOCTOR_ID, {
                "consultId": consult_id,
                "createdAt": at,
                "status": "APPROVED",
                "diagnosis": visit["diagnosis"],
                "rxId": rx_id,
            })
            store.put_prescription(patient["patientId"], DOCTOR_ID, {
                **visit,
                "rxId": rx_id,
                "createdAt": at,
                "consultId": consult_id,
                "status": "APPROVED",
                "approvedAt": at,
                "sentVia": "whatsapp",
            })
            for m in visit["medicines"]:
                entry = shortlist.setdefault(m["brand_id"], {"useCount": 0, "lastUsedAt": at})
                entry["useCount"] += 1
                entry["lastUsedAt"] = max(entry["lastUsedAt"], at)
            summary["prescriptions"] += 1
        summary["patients"].append(patient["patientId"])
    for brand_id, entry in shortlist.items():
        store.add_to_shortlist(DOCTOR_ID, brand_id, **entry)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint-url", help="DynamoDB Local endpoint, e.g. http://localhost:8000")
    parser.add_argument("--create", action="store_true", help="create the app table if missing")
    args = parser.parse_args(argv)

    db = dynamodb_resource(endpoint_url=args.endpoint_url)
    if args.create:
        create_tables(db, names=[MEDSCRIBE_TABLE])
    out = seed(Store(db))
    print(f"doctor {out['doctorId']}; patients {', '.join(out['patients'])}; "
          f"{out['prescriptions']} prescriptions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
