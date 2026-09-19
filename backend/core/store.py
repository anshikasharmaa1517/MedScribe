"""DynamoDB access layer for the single-table design in CLAUDE_CODE_CONTEXT.md §7.

One app table `medscribe` keyed PK/SK, with GSI1 (doctorId / SK) for a doctor's
patient list and today's consults. Three reference tables hold the seed CSVs.

Key shapes are built only here, so the rest of the code never assembles a
`PAT#...` string by hand. Time-ordered SKs embed a UTC ISO timestamp, which is
what makes `begins_with(CONSULT#2026-09-19)` mean "today".
"""
import csv
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

from core.settings import (
    AWS_REGION,
    BRANDS_TABLE,
    CONDITIONS_TABLE,
    DYNAMODB_ENDPOINT_URL,
    MEDSCRIBE_TABLE,
    SALTS_TABLE,
)

GSI1 = "GSI1"

TABLE_DEFINITIONS = {
    MEDSCRIBE_TABLE: {
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "doctorId", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": GSI1,
                "KeySchema": [
                    {"AttributeName": "doctorId", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    },
    BRANDS_TABLE: {
        "KeySchema": [{"AttributeName": "brand_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "brand_id", "AttributeType": "S"}],
    },
    SALTS_TABLE: {
        "KeySchema": [{"AttributeName": "salt_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "salt_id", "AttributeType": "S"}],
    },
    CONDITIONS_TABLE: {
        "KeySchema": [{"AttributeName": "condition_id", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "condition_id", "AttributeType": "S"}],
    },
}

PIPE_LIST_COLUMNS = {
    "salt_ids", "aliases", "condition_ids", "common_strengths", "synonyms", "expected_salt_classes",
}


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def dynamodb_resource(endpoint_url=None, region=None):
    return boto3.resource(
        "dynamodb",
        region_name=region or AWS_REGION,
        endpoint_url=endpoint_url or DYNAMODB_ENDPOINT_URL,
    )


def create_tables(dynamodb, names=None):
    existing = {t.name for t in dynamodb.tables.all()}
    for name in names or TABLE_DEFINITIONS:
        if name in existing:
            continue
        dynamodb.create_table(
            TableName=name, BillingMode="PAY_PER_REQUEST", **TABLE_DEFINITIONS[name]
        )
        dynamodb.Table(name).wait_until_exists()


def to_dynamo(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamo(v) for v in value]
    return value


def from_dynamo(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


def parse_csv_row(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        v = v.strip() if isinstance(v, str) else v
        if k in PIPE_LIST_COLUMNS:
            out[k] = [x.strip() for x in v.split("|") if x.strip()] if v else []
        else:
            out[k] = v
    return out


def load_csv(path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return [parse_csv_row(r) for r in csv.DictReader(f)]


class Store:
    def __init__(self, dynamodb=None, table_name=MEDSCRIBE_TABLE):
        self.db = dynamodb or dynamodb_resource()
        self.table = self.db.Table(table_name)
        self.brands = self.db.Table(BRANDS_TABLE)
        self.salts = self.db.Table(SALTS_TABLE)
        self.conditions = self.db.Table(CONDITIONS_TABLE)

    # -- internals --------------------------------------------------------

    def _put(self, item: dict):
        self.table.put_item(Item=to_dynamo(item))

    def _get(self, pk: str, sk: str):
        item = self.table.get_item(Key={"PK": pk, "SK": sk}).get("Item")
        return from_dynamo(item) if item else None

    def _query(self, pk, sk_prefix=None, index=None, newest_first=False, limit=None, entity=None):
        cond = Key("doctorId" if index == GSI1 else "PK").eq(pk)
        if sk_prefix:
            cond &= Key("SK").begins_with(sk_prefix)
        kwargs = {"KeyConditionExpression": cond, "ScanIndexForward": not newest_first}
        if index:
            kwargs["IndexName"] = index
        if entity:
            kwargs["FilterExpression"] = Attr("entity").eq(entity)
        if limit:
            kwargs["Limit"] = limit
        items = []
        while True:
            page = self.table.query(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page or (limit and len(items) >= limit):
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        if limit:
            items = items[:limit]
        return [from_dynamo(i) for i in items]

    # -- doctor -------------------------------------------------------------

    def put_doctor(self, doctor: dict) -> dict:
        doctor = {**doctor, "doctorId": doctor.get("doctorId") or new_id(), "entity": "DOCTOR"}
        self._put({"PK": f"DOC#{doctor['doctorId']}", "SK": "PROFILE", **doctor})
        return doctor

    def get_doctor(self, doctor_id: str):
        return self._get(f"DOC#{doctor_id}", "PROFILE")

    # -- patient + phone lookup -------------------------------------------

    def put_patient(self, patient: dict) -> dict:
        patient_id = patient.get("patientId") or new_id()
        patient = {**patient, "patientId": patient_id, "entity": "PATIENT"}
        self._put({"PK": f"PAT#{patient['patientId']}", "SK": "PROFILE", **patient})
        if patient.get("phone"):
            self._put({
                "PK": f"PHONE#{patient['phone']}",
                "SK": "PATIENT",
                "entity": "PHONE",
                "patientId": patient["patientId"],
            })
        return patient

    def get_patient(self, patient_id: str):
        return self._get(f"PAT#{patient_id}", "PROFILE")

    def get_patient_by_phone(self, phone_e164: str):
        lookup = self._get(f"PHONE#{phone_e164}", "PATIENT")
        return self.get_patient(lookup["patientId"]) if lookup else None

    def list_patients_for_doctor(self, doctor_id: str) -> list[dict]:
        return self._query(doctor_id, "PROFILE", index=GSI1, entity="PATIENT")

    # -- consultation -----------------------------------------------------

    def put_consultation(self, patient_id: str, doctor_id: str, consult: dict) -> dict:
        consult = {
            **consult,
            "consultId": consult.get("consultId") or new_id(),
            "createdAt": consult.get("createdAt") or now_iso(),
            "patientId": patient_id,
            "doctorId": doctor_id,
            "entity": "CONSULT",
        }
        self._put({
            "PK": f"PAT#{patient_id}",
            "SK": f"CONSULT#{consult['createdAt']}#{consult['consultId']}",
            **consult,
        })
        return consult

    def create_consult(self, patient_id: str, doctor_id: str, consult: dict | None = None) -> dict:
        """put_consultation plus a CONSULT#<id> lookup row so routes can address by id alone."""
        c = self.put_consultation(patient_id, doctor_id, consult or {})
        self._put({
            "PK": f"CONSULT#{c['consultId']}", "SK": "META", "entity": "CONSULT_LOOKUP",
            "patientId": patient_id, "createdAt": c["createdAt"], "consultId": c["consultId"],
        })
        return c

    def get_consult_by_id(self, consult_id: str):
        meta = self._get(f"CONSULT#{consult_id}", "META")
        if not meta:
            return None
        return self.get_consultation(meta["patientId"], meta["createdAt"], consult_id)

    def update_consultation(self, consult: dict, **fields) -> dict:
        merged = {**consult, **fields}
        self.put_consultation(consult["patientId"], consult["doctorId"], merged)
        return merged

    def get_consultation(self, patient_id: str, created_at: str, consult_id: str):
        return self._get(f"PAT#{patient_id}", f"CONSULT#{created_at}#{consult_id}")

    def list_consultations(self, patient_id: str, newest_first=True, limit=None) -> list[dict]:
        return self._query(f"PAT#{patient_id}", "CONSULT#", newest_first=newest_first, limit=limit)

    def list_consultations_for_doctor(self, doctor_id: str, day: str | None = None) -> list[dict]:
        prefix = f"CONSULT#{day}" if day else "CONSULT#"
        return self._query(doctor_id, prefix, index=GSI1, newest_first=True)

    # -- prescription -----------------------------------------------------

    def put_prescription(self, patient_id: str, doctor_id: str, rx: dict) -> dict:
        rx = {
            **rx,
            "rxId": rx.get("rxId") or new_id(),
            "createdAt": rx.get("createdAt") or now_iso(),
            "patientId": patient_id,
            "doctorId": doctor_id,
            "entity": "RX",
        }
        self._put({"PK": f"PAT#{patient_id}", "SK": f"RX#{rx['createdAt']}#{rx['rxId']}", **rx})
        return rx

    def get_prescription(self, patient_id: str, created_at: str, rx_id: str):
        return self._get(f"PAT#{patient_id}", f"RX#{created_at}#{rx_id}")

    def list_prescriptions(self, patient_id: str, newest_first=True, limit=None) -> list[dict]:
        return self._query(f"PAT#{patient_id}", "RX#", newest_first=newest_first, limit=limit)

    # -- reminder ---------------------------------------------------------

    def put_reminder(self, patient_id: str, reminder: dict) -> dict:
        reminder = {
            **reminder,
            "remId": reminder.get("remId") or new_id(),
            "dueAt": reminder["dueAt"],
            "patientId": patient_id,
            "status": reminder.get("status", "SCHEDULED"),
            "entity": "REMINDER",
        }
        self._put({
            "PK": f"PAT#{patient_id}",
            "SK": f"REM#{reminder['dueAt']}#{reminder['remId']}",
            **reminder,
        })
        return reminder

    def list_reminders(self, patient_id: str, from_iso: str | None = None) -> list[dict]:
        items = self._query(f"PAT#{patient_id}", "REM#")
        return [r for r in items if not from_iso or r["dueAt"] >= from_iso]

    def get_reminder(self, patient_id: str, due_at: str, rem_id: str):
        return self._get(f"PAT#{patient_id}", f"REM#{due_at}#{rem_id}")

    def set_reminder_fields(self, patient_id: str, due_at: str, rem_id: str, **fields):
        fields["updatedAt"] = now_iso()
        names = {f"#f{i}": k for i, k in enumerate(fields)}
        values = {f":v{i}": to_dynamo(v) for i, v in enumerate(fields.values())}
        self.table.update_item(
            Key={"PK": f"PAT#{patient_id}", "SK": f"REM#{due_at}#{rem_id}"},
            UpdateExpression="SET " + ", ".join(f"{n} = :v{i}" for i, n in enumerate(names)),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

    def set_reminder_status(self, patient_id: str, due_at: str, rem_id: str, status: str):
        self.set_reminder_fields(patient_id, due_at, rem_id, status=status)

    def touch_patient_inbound(self, patient_id: str, at: str | None = None):
        """Record the patient's latest inbound WhatsApp message (opens the 24 h window)."""
        self.table.update_item(
            Key={"PK": f"PAT#{patient_id}", "SK": "PROFILE"},
            UpdateExpression="SET lastInboundAt = :t",
            ExpressionAttributeValues={":t": at or now_iso()},
        )

    # -- doctor shortlist (Tier 1) ----------------------------------------

    def add_to_shortlist(self, doctor_id: str, brand_id: str, **attrs) -> dict:
        item = {"doctorId": doctor_id, "brandId": brand_id, "entity": "SHORTLIST", **attrs}
        self._put({"PK": f"DOC#{doctor_id}", "SK": f"BRAND#{brand_id}", **item})
        return item

    def bump_shortlist(self, doctor_id: str, brand_id: str) -> int:
        out = self.table.update_item(
            Key={"PK": f"DOC#{doctor_id}", "SK": f"BRAND#{brand_id}"},
            UpdateExpression=(
                "SET doctorId = :d, brandId = :b, entity = :e, lastUsedAt = :t "
                "ADD useCount :one"
            ),
            ExpressionAttributeValues={
                ":d": doctor_id, ":b": brand_id, ":e": "SHORTLIST", ":t": now_iso(), ":one": 1,
            },
            ReturnValues="UPDATED_NEW",
        )
        return int(out["Attributes"]["useCount"])

    def list_shortlist(self, doctor_id: str) -> list[dict]:
        return self._query(f"DOC#{doctor_id}", "BRAND#")

    def remove_from_shortlist(self, doctor_id: str, brand_id: str):
        self.table.delete_item(Key={"PK": f"DOC#{doctor_id}", "SK": f"BRAND#{brand_id}"})

    # -- review queue (Tier 2) --------------------------------------------
    # Proposals live only here. Nothing in this module copies a proposal into a
    # reference table: promotion to Tier 0 is a human action in the admin UI.

    def enqueue_proposal(self, proposal: dict) -> dict:
        proposal = {
            **proposal,
            "propId": proposal.get("propId") or new_id(),
            "createdAt": proposal.get("createdAt") or now_iso(),
            "status": "PENDING",
            "entity": "PROPOSAL",
        }
        self._put({
            "PK": "QUEUE",
            "SK": f"PENDING#{proposal['createdAt']}#{proposal['propId']}",
            **proposal,
        })
        return proposal

    def list_pending_proposals(self) -> list[dict]:
        return [p for p in self._query("QUEUE", "PENDING#") if p["status"] == "PENDING"]

    def review_proposal(self, created_at: str, prop_id: str, decision: str, reviewer: str):
        if decision not in ("APPROVED", "REJECTED"):
            raise ValueError("decision must be APPROVED or REJECTED")
        self.table.update_item(
            Key={"PK": "QUEUE", "SK": f"PENDING#{created_at}#{prop_id}"},
            UpdateExpression="SET #s = :s, reviewedBy = :r, reviewedAt = :t",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": decision, ":r": reviewer, ":t": now_iso()},
        )

    # -- reference tables (Tier 0) ----------------------------------------

    def load_reference_rows(self, table, rows: list[dict]) -> int:
        with table.batch_writer() as batch:
            for r in rows:
                batch.put_item(Item=to_dynamo(r))
        return len(rows)

    def get_brand(self, brand_id: str):
        item = self.brands.get_item(Key={"brand_id": brand_id}).get("Item")
        return from_dynamo(item) if item else None

    def get_salt(self, salt_id: str):
        item = self.salts.get_item(Key={"salt_id": salt_id}).get("Item")
        return from_dynamo(item) if item else None

    def get_condition(self, condition_id: str):
        item = self.conditions.get_item(Key={"condition_id": condition_id}).get("Item")
        return from_dynamo(item) if item else None

    def count_reference(self, table) -> int:
        return table.scan(Select="COUNT")["Count"]
