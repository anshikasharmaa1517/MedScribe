"""Pipeline: transcript -> extract -> resolve -> validate -> draft state.

`process()` is a pure function of (transcript, prior_state); the caller owns
persistence. Each extraction pass re-reads the whole transcript, so the merge
with prior state is what keeps a doctor's edits and previously seen medicines
from being wiped by the next 15-second cycle.
"""
import logging
import re

from core.extractor import extract
from core.llm import LLMError
from core.resolver import Resolver
from core.validator import Validator

log = logging.getLogger(__name__)

TOP_LEVEL_FIELDS = ("symptoms", "diagnosis", "tests_advised", "next_visit")
MED_FIELDS = ("frequency", "food_relation", "duration")

_resolver = None
_validator = None


def _resolver_instance():
    global _resolver
    if _resolver is None:
        _resolver = Resolver()
    return _resolver


def _validator_instance():
    global _validator
    if _validator is None:
        _validator = Validator()
    return _validator


def empty_draft() -> dict:
    return {
        "symptoms": [],
        "diagnosis": None,
        "conditions_matched": [],
        "tests_advised": [],
        "medicines": [],
        "duplicate_salts": [],
        "next_visit": None,
        "locked_fields": [],
        "blocks_approval": False,
        "approval_blocked_by": [],
    }


def med_key(brand_id, spoken) -> str:
    if brand_id:
        return brand_id
    return re.sub(r"[^a-z0-9]+", " ", spoken.lower()).strip()


def _resolve_one(ex_med: dict, resolver) -> dict:
    spoken = ex_med.get("spoken_name") or ""
    r = resolver.resolve(spoken)
    item = {
        "spoken": spoken,
        "brand_id": r["brand_id"],
        "matched": r["matched"],
        "salt_ids": r["salt_ids"],
        "status": r["status"],
        "reason": r["reason"],
        "alternatives": r["alternatives"],
        "locked": False,
        "deleted": False,
    }
    for f in MED_FIELDS:
        item[f] = ex_med.get(f)
    item["med_key"] = med_key(item["brand_id"], spoken)
    return item


def _merge_medicines(prior_meds: list, new_meds: list) -> list:
    new_by_key = {}
    for m in new_meds:
        new_by_key.setdefault(m["med_key"], m)

    merged = []
    seen = set()
    for pm in prior_meds:
        k = pm["med_key"]
        seen.add(k)
        if pm.get("locked") or pm.get("deleted") or k not in new_by_key:
            merged.append(dict(pm))
        else:
            merged.append(new_by_key[k])
    for m in new_meds:
        if m["med_key"] not in seen:
            seen.add(m["med_key"])
            merged.append(m)
    return merged


def process(transcript_text: str, prior_state: dict | None = None, client=None) -> dict:
    prior = prior_state or empty_draft()

    try:
        ex = extract(transcript_text, client=client)
    except LLMError as e:
        log.warning("extraction failed, keeping prior state: %s", e)
        return {**prior, "extraction_error": str(e)}

    state = dict(prior)
    state.pop("extraction_error", None)
    locked_fields = set(prior.get("locked_fields", []))
    for f in TOP_LEVEL_FIELDS:
        if f not in locked_fields:
            state[f] = ex.get(f) if f in ("diagnosis", "next_visit") else list(ex.get(f) or [])
    state["locked_fields"] = sorted(locked_fields)

    resolver = _resolver_instance()
    new_meds = [_resolve_one(m, resolver) for m in ex.get("medicines") or []]
    merged = _merge_medicines(prior.get("medicines", []), new_meds)

    active = [m for m in merged if not m.get("deleted")]
    validated = _validator_instance().validate(active, state["diagnosis"])
    by_key = {m["med_key"]: m for m in validated["medicines"]}
    for m in merged:
        if m.get("deleted"):
            continue
        v = by_key[m["med_key"]]
        m["clinical"] = v["clinical"]
        m["clinical_reason"] = v["clinical_reason"]
        if not m.get("locked"):
            m["status"] = v["status"]

    state["medicines"] = merged
    state["conditions_matched"] = validated["conditions_matched"]
    state["duplicate_salts"] = validated["duplicate_salts"]
    blocked = [m["med_key"] for m in active if m["status"] == "RESOLVE"]
    state["approval_blocked_by"] = blocked
    state["blocks_approval"] = bool(blocked)
    return state
