"""Pipeline: transcript -> extract -> resolve -> validate -> draft state.

`process()` is a pure function of (transcript, prior_state); the caller owns
persistence. Each extraction pass re-reads the whole transcript, so the merge
with prior state is what keeps a doctor's edits and previously seen medicines
from being wiped by the next 15-second cycle.
"""
import hashlib
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
    """Stable identity for a medicine across passes: brand_id once resolved, else the
    normalised spoken name. Unicode-aware, so a Devanagari name ("डोलो फाइव हंड्रेड")
    keeps a real key instead of collapsing to an empty string."""
    if brand_id:
        return brand_id
    key = re.sub(r"[\s!-/:-@\[-`{-~]+", " ", (spoken or "").casefold()).strip()
    return key or "spoken:" + hashlib.sha1((spoken or "").encode()).hexdigest()[:8]


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


def _same_medicine(prior: dict, new: dict) -> bool:
    """A new extraction hit refers to a prior item if the keys match, or if both
    resolve to the same brand. The second case is what keeps a doctor-resolved
    item from being duplicated when the next pass hears the name differently."""
    if prior["med_key"] == new["med_key"]:
        return True
    return bool(prior.get("brand_id")) and prior.get("brand_id") == new.get("brand_id")


def _merge_medicines(prior_meds: list, new_meds: list) -> list:
    merged = []
    consumed: set[int] = set()
    for pm in prior_meds:
        match = next((i for i, m in enumerate(new_meds)
                      if i not in consumed and _same_medicine(pm, m)), None)
        if match is not None:
            consumed.add(match)
        if match is None or pm.get("locked") or pm.get("deleted"):
            merged.append(dict(pm))
        else:
            merged.append({**new_meds[match], "med_key": pm["med_key"]})
    seen_keys = {m["med_key"] for m in merged}
    for i, m in enumerate(new_meds):
        if i in consumed or m["med_key"] in seen_keys:
            continue
        seen_keys.add(m["med_key"])
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

    state["medicines"] = merged
    return validate_state(state)


def validate_state(state: dict) -> dict:
    """Recompute clinical verdicts, duplicates and approval blocking in place.

    Called after every extraction pass and after every doctor edit. Locked
    medicines keep their doctor-set status; only the deterministic clinical
    fields are refreshed.
    """
    merged = state["medicines"]
    active = [m for m in merged if not m.get("deleted")]
    validated = _validator_instance().validate(active, state.get("diagnosis"))
    by_key = {m["med_key"]: m for m in validated["medicines"]}
    for m in merged:
        if m.get("deleted"):
            continue
        v = by_key[m["med_key"]]
        m["clinical"] = v["clinical"]
        m["clinical_reason"] = v["clinical_reason"]
        if not m.get("locked"):
            m["status"] = v["status"]

    state["conditions_matched"] = validated["conditions_matched"]
    state["duplicate_salts"] = validated["duplicate_salts"]
    blocked = [m["med_key"] for m in active if m["status"] == "RESOLVE"]
    state["approval_blocked_by"] = blocked
    state["blocks_approval"] = bool(blocked)
    return state
