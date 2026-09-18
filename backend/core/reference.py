"""Tier 0 reference lookups (brand, salt, condition) backed by the seed CSVs.

The CSVs ship inside the Lambda package alongside the resolver's own index, so
reading them here avoids three DynamoDB round-trips per prescription and keeps
the PDF renderer usable in tests and scripts without any AWS at all.
"""
from functools import lru_cache

from core.settings import BRANDS_CSV, CONDITIONS_CSV, SALTS_CSV
from core.store import load_csv


class Reference:
    def __init__(self, brands: dict, salts: dict, conditions: dict):
        self.brands = brands
        self.salts = salts
        self.conditions = conditions

    @classmethod
    def from_csv(cls, brands_csv=BRANDS_CSV, salts_csv=SALTS_CSV, conditions_csv=CONDITIONS_CSV):
        return cls(
            {r["brand_id"]: r for r in load_csv(brands_csv)},
            {r["salt_id"]: r for r in load_csv(salts_csv)},
            {r["condition_id"]: r for r in load_csv(conditions_csv)},
        )

    def get_brand(self, brand_id):
        return self.brands.get(brand_id)

    def get_salt(self, salt_id):
        return self.salts.get(salt_id)

    def get_condition(self, condition_id):
        return self.conditions.get(condition_id)

    def search_brands(self, q: str, limit: int = 20) -> list[dict]:
        needle = q.strip().lower()
        if not needle:
            return []
        hits = []
        for b in self.brands.values():
            label = brand_label(b)
            if needle in label.lower() or any(needle in a for a in b.get("aliases") or []):
                hits.append({"brand_id": b["brand_id"], "label": label, "salt_ids": b["salt_ids"]})
                if len(hits) >= limit:
                    break
        return hits


def brand_label(brand: dict) -> str:
    return (f"{brand['base_brand']} {brand.get('modifier', '')} "
            f"{brand.get('strength', '')}{brand.get('unit', '')}").replace("  ", " ").strip()


@lru_cache(maxsize=1)
def default_reference() -> Reference:
    return Reference.from_csv()
