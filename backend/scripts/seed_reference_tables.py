"""Load the three seed CSVs into the Tier 0 reference tables.

Run from backend/:
    python -m scripts.seed_reference_tables               # tables must exist
    python -m scripts.seed_reference_tables --create      # create them first (dev / DynamoDB Local)
    python -m scripts.seed_reference_tables --endpoint-url http://localhost:8000 --create

This is the ONLY code path that writes to brands / salts / conditions. Nothing
derived from a web search or an LLM goes through here (rule 6).
"""
import argparse
import sys

from core.settings import (
    BRANDS_CSV,
    BRANDS_TABLE,
    CONDITIONS_CSV,
    CONDITIONS_TABLE,
    SALTS_CSV,
    SALTS_TABLE,
)
from core.store import Store, create_tables, dynamodb_resource, load_csv

SOURCES = (
    (BRANDS_TABLE, BRANDS_CSV),
    (SALTS_TABLE, SALTS_CSV),
    (CONDITIONS_TABLE, CONDITIONS_CSV),
)


def seed(store: Store) -> dict[str, int]:
    counts = {}
    for table_name, csv_path in SOURCES:
        table = store.db.Table(table_name)
        counts[table_name] = store.load_reference_rows(table, load_csv(csv_path))
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint-url", help="DynamoDB Local endpoint, e.g. http://localhost:8000")
    parser.add_argument("--create", action="store_true", help="create reference tables if missing")
    args = parser.parse_args(argv)

    db = dynamodb_resource(endpoint_url=args.endpoint_url)
    if args.create:
        create_tables(db, names=[t for t, _ in SOURCES])
    for table, n in seed(Store(db)).items():
        print(f"{table:12} {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
