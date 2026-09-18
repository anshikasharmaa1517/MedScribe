"""Smoke test: the vendored modules import and load their seed data from backend/data/.

If this fails, nothing downstream is worth debugging.
"""
from core.resolver import Resolver
from core.validator import Validator


def test_resolver_loads_seed_brands():
    assert len(Resolver().brands) == 220


def test_validator_loads_seed_salts_and_conditions():
    v = Validator()
    assert len(v.salts) == 120
    assert len(v.conds) == 20


def test_resolves_dolo_650():
    result = Resolver().resolve("dolo 650")

    assert result["status"] == "AUTO"
    assert result["brand_id"] == "B001"
    assert result["salt_ids"] == ["S001"]
    assert "Dolo" in result["matched"]


def test_validate_runs_end_to_end():
    meds = [Resolver().resolve("dolo 650")]
    result = Validator().validate(meds, "viral fever")

    assert result["conditions_matched"] == ["Viral fever"]
    assert result["medicines"][0]["clinical"] == "OK"
    assert result["duplicate_salts"] == []
