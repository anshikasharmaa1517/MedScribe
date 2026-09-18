"""Extracts structured consultation data from a Hinglish transcript via the configured LLM.

The system prompt lives in `prompts/extract_system.txt` so it can be iterated on
without a code change. This module only owns the call and a defensive parse
that pins the model's output to the schema in BUILD_TASK §Component 1.

Never corrects or normalises a medicine name here - that is the resolver's job.
Pre-correcting a spoken name destroys the signal the matcher needs to detect
ASR errors, so `spoken_name` passes through untouched.
"""
import logging
from functools import lru_cache
from pathlib import Path

from core.llm import LLMResponseError, get_client
from core.settings import EXTRACT_MAX_TOKENS, EXTRACT_PROMPT_PATH

log = logging.getLogger(__name__)

LIST_FIELDS = ("symptoms", "tests_advised")
SCALAR_FIELDS = ("diagnosis", "next_visit")
MED_FIELDS = ("frequency", "food_relation", "duration")


@lru_cache(maxsize=1)
def system_prompt() -> str:
    return Path(EXTRACT_PROMPT_PATH).read_text(encoding="utf-8").strip()


def build_user_message(transcript_text: str) -> str:
    return f"Transcript:\n{transcript_text}\n\nExtract the structured data now."


def _scalar(value, field):
    if value is None or isinstance(value, str):
        return value or None
    raise LLMResponseError(f"{field} must be a string or null, got {type(value).__name__}")


def _str_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list):
        raise LLMResponseError(f"{field} must be a list, got {type(value).__name__}")
    if not all(isinstance(v, str) for v in value):
        raise LLMResponseError(f"{field} must contain only strings")
    return [v.strip() for v in value if v.strip()]


def _medicine(raw, index):
    if not isinstance(raw, dict):
        raise LLMResponseError(f"medicines[{index}] must be an object")
    spoken = raw.get("spoken_name")
    if not isinstance(spoken, str) or not spoken.strip():
        raise LLMResponseError(f"medicines[{index}] has no spoken_name")
    # Rule 1: the LLM never sets an identifier. Anything id-shaped is discarded
    # here so nothing downstream can ever read it, even by accident.
    ids = [k for k in raw if k not in ("spoken_name", *MED_FIELDS)]
    if ids:
        log.warning("extractor discarded unexpected medicine keys %s", ids)
    med = {"spoken_name": spoken.strip()}
    for f in MED_FIELDS:
        med[f] = _scalar(raw.get(f), f"medicines[{index}].{f}")
    return med


def normalise(payload) -> dict:
    """Coerce model output onto the exact extraction schema or raise LLMResponseError.

    Raising (rather than dropping the bad part) is what lets the glue fall back
    to the previous good state - a half-parsed draft is worse than a stale one.
    """
    if not isinstance(payload, dict):
        raise LLMResponseError(f"extraction must be a JSON object, got {type(payload).__name__}")
    out = {}
    for f in LIST_FIELDS:
        out[f] = _str_list(payload.get(f), f)
    for f in SCALAR_FIELDS:
        out[f] = _scalar(payload.get(f), f)
    meds = payload.get("medicines")
    if meds is None:
        meds = []
    if not isinstance(meds, list):
        raise LLMResponseError(f"medicines must be a list, got {type(meds).__name__}")
    out["medicines"] = [_medicine(m, i) for i, m in enumerate(meds)]
    return out


def extract(transcript_text: str, client=None) -> dict:
    client = client or get_client()
    raw = client.converse_json(
        system_prompt(), build_user_message(transcript_text), max_tokens=EXTRACT_MAX_TOKENS
    )
    return normalise(raw)
