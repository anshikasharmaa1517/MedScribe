"""GET /health - proves the deploy: Lambda runs, IAM reaches the table, SSM prefix resolves.

Dependency-free on purpose (only the runtime's boto3), so it deploys without a
layer. Real handlers import core/ and ship with the dependency layer.
"""
import json
import os

import boto3

REGION = os.environ.get("AWS_REGION", "ap-south-1")


def _table_status(name: str) -> str:
    try:
        return boto3.client("dynamodb", region_name=REGION).describe_table(TableName=name)[
            "Table"
        ]["TableStatus"]
    except Exception as e:  # noqa: BLE001 - health endpoint reports, never raises
        return f"error: {type(e).__name__}"


def _secrets_present(prefix: str) -> list[str]:
    try:
        page = boto3.client("ssm", region_name=REGION).get_parameters_by_path(
            Path=prefix, WithDecryption=False
        )
        return sorted(p["Name"].rsplit("/", 1)[-1] for p in page.get("Parameters", []))
    except Exception as e:  # noqa: BLE001
        return [f"error: {type(e).__name__}"]


def handler(event, context):
    table = os.environ.get("MEDSCRIBE_TABLE", "")
    prefix = os.environ.get("SSM_PREFIX", "")
    body = {
        "ok": True,
        "service": "medscribe",
        "env": os.environ.get("ENV"),
        "region": REGION,
        "table": {"name": table, "status": _table_status(table) if table else "unset"},
        "ssm_prefix": prefix,
        "secrets_present": _secrets_present(prefix) if prefix else [],
    }
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
