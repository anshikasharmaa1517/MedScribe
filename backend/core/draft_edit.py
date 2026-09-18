"""Doctor edits to a draft (the DraftPatch in docs/API_CONTRACT.md).

Pure: takes the stored draft and a patch, returns the new draft. Every edit
locks what it touched so the next extraction pass leaves it alone (rule 4), and
nothing here ever removes a medicine - `deleted` is a tombstone (rule 3).
"""
from core.pipeline import TOP_LEVEL_FIELDS, med_key, validate_state
from core.reference import Reference, brand_label, default_reference

MED_DOSE_FIELDS = ("frequency", "food_relation", "duration")


class PatchError(ValueError):
    """The patch is malformed or refers to something that does not exist."""


def _set_brand(med: dict, brand: dict) -> None:
    med["brand_id"] = brand["brand_id"]
    med["matched"] = brand_label(brand)
    med["salt_ids"] = list(brand.get("salt_ids") or [])
    med["status"] = "AUTO"
    med["reason"] = "set by doctor"
    med["alternatives"] = []


def _find(state: dict, key: str) -> dict:
    for m in state["medicines"]:
        if m["med_key"] == key:
            return m
    raise PatchError(f"no medicine with med_key {key!r}")


def _apply_medicine(state: dict, p: dict, ref: Reference) -> None:
    if "add" in p:
        add = p["add"]
        brand = ref.get_brand(add.get("brand_id"))
        if not brand:
            raise PatchError(f"unknown brand_id {add.get('brand_id')!r}")
        label = brand_label(brand)
        key = brand["brand_id"]
        if any(m["med_key"] == key and not m.get("deleted") for m in state["medicines"]):
            raise PatchError(f"{label} is already on the prescription")
        med = {
            "spoken": label, "brand_id": None, "matched": None, "salt_ids": [], "status": "AUTO",
            "reason": "", "alternatives": [], "locked": True, "deleted": False, "med_key": key,
            "clinical": "PENDING_CHECK", "clinical_reason": "",
        }
        _set_brand(med, brand)
        med["reason"] = "added by doctor"
        for f in MED_DOSE_FIELDS:
            med[f] = add.get(f)
        state["medicines"].append(med)
        return

    if "med_key" not in p:
        raise PatchError("medicine patch needs med_key or add")
    med = _find(state, p["med_key"])
    med["locked"] = True

    if p.get("deleted") is True:
        med["deleted"] = True
    elif p.get("confirm") is True:
        if med["status"] == "RESOLVE":
            raise PatchError("a RESOLVE item cannot be confirmed; pick a brand")
        med["status"] = "AUTO"
        med["reason"] = "confirmed by doctor"
    elif "brand_id" in p or "accept_alternative" in p:
        bid = p.get("brand_id") or p.get("accept_alternative")
        brand = ref.get_brand(bid)
        if not brand:
            raise PatchError(f"unknown brand_id {bid!r}")
        _set_brand(med, brand)
    else:
        touched = False
        for f in MED_DOSE_FIELDS:
            if f in p:
                med[f] = p[f] or None
                touched = True
        if not touched:
            raise PatchError("medicine patch changes nothing")


def apply_patch(state: dict, patch: dict, ref: Reference | None = None) -> dict:
    ref = ref or default_reference()
    state = {**state, "medicines": [dict(m) for m in state.get("medicines", [])]}
    state.pop("extraction_error", None)

    if "field" in patch:
        field = patch["field"]
        if field not in TOP_LEVEL_FIELDS:
            raise PatchError(f"unknown field {field!r}")
        value = patch.get("value")
        if field in ("symptoms", "tests_advised"):
            if not isinstance(value, list):
                raise PatchError(f"{field} needs a list")
            value = [str(v).strip() for v in value if str(v).strip()]
        else:
            value = (str(value).strip() or None) if value is not None else None
        state[field] = value
        state["locked_fields"] = sorted(set(state.get("locked_fields", [])) | {field})
    elif "medicine" in patch and isinstance(patch["medicine"], dict):
        _apply_medicine(state, patch["medicine"], ref)
    else:
        raise PatchError("patch must have 'field' or 'medicine'")

    for m in state["medicines"]:
        if not m.get("med_key"):
            m["med_key"] = med_key(m.get("brand_id"), m.get("spoken", ""))
    return validate_state(state)
