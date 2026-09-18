# MedScribe — house rules

Full spec: `docs/CLAUDE_CODE_CONTEXT.md`. Current build task: `docs/BUILD_TASK_extraction_layer.md`.

## Workflow

Work on `main` and push directly to it. No feature branches, no pull requests —
the repo owner reviews by pulling. Hackathon pace, single author.

Commit messages: clean and short (a single summary line is fine), no Claude/AI
attribution or co-author lines.

## The seven non-negotiables

1. The LLM **never** sets `brand_id`. Only `resolver.py` does.
2. The LLM returns `spoken_name` **verbatim**. Never normalised or corrected —
   pre-correcting destroys the signal the matcher needs.
3. **Nothing is ever silently dropped.** A `RESOLVE` item stays on screen and blocks
   approval. A missing medicine is more dangerous than a flagged wrong one.
4. **Doctor edits win.** Once a field is touched, set `locked: true` and stop
   overwriting it on later extraction passes.
5. The LLM verifier may **upgrade** an item to CONFIRM. It may never resolve a name,
   remove an item, or clear a deterministic flag.
6. No web-search result is ever written into Tier 0 or Tier 1.
7. Nothing reaches the patient without doctor approval.

## Do not rewrite

`backend/core/resolver.py` and `backend/core/validator.py` are vendored. They are
exempt from lint reformatting in `pyproject.toml`. The only changes made to them:

| File | Change |
|---|---|
| `core/resolver.py` | added `from core.settings import BRANDS_CSV, AUTO_SCORE, MIN_SCORE, MIN_MARGIN`; `__init__` default `'seed_brands.csv'` → `BRANDS_CSV`; the three threshold constants deleted (now read from settings, pinned by `tests/test_resolver_contract.py`) |
| `core/validator.py` | added `from core.settings import CONDITIONS_CSV, SALTS_CSV`; `__init__` defaults → those constants; `__main__` import → `from core.resolver import Resolver` |

No matching, scoring or clinical logic was touched. Before changing anything else in
them, write a contract test pinning current behaviour first.

## Decisions taken (open questions from the spec review)

- **`process()` signature** → `process(transcript_text, prior_state=None) -> dict`.
  Pure function; the caller owns persistence. The spec's bare
  `process(transcript_text)` cannot satisfy locking or fail-safe.
- **Medicine identity across passes** → each medicine carries a stable `med_key`:
  `brand_id` when resolved, else the normalised spoken name; ties broken by order of
  first appearance. Locked fields reattach by `med_key`, never by list index —
  index order shifts the moment a drug is added mid-list.
- **Merge policy** → union with tombstones. A medicine the LLM stops emitting
  persists (rule 3); one the doctor explicitly deletes stays deleted. Idempotency
  means `f(transcript, prior_state)` is deterministic, not that output ignores state.
- **Thresholds** → `AUTO_SCORE` / `MIN_SCORE` / `MIN_MARGIN` and
  `EXTRACTION_INTERVAL_SECONDS` live in `core/settings.py`, env-overridable. `MIN_SCORE`
  is 72 (was 62) so a nonsense name returns `RESOLVE` rather than `CONFIRM` — at 62,
  `paracetamol` resolved to Stamlo 5mg (a BP drug) as `CONFIRM`. Done, behind
  `tests/test_resolver_contract.py`.
- **LASA flag** → the demo's centrepiece currently cannot fire. `LASA_PAIRS` is dead
  code, Amiloride (S073) has no `condition_ids` so it returns `UNKNOWN` not
  `CONTRADICTS`, and Amlodipine is in a different phonetic bucket so it never appears
  as an alternative. Fix: add condition C021 (oedema / heart failure), map S073 to it,
  re-run `make rebuild-seed`, and implement the LASA check as a post-validator pass in
  the glue layer — `validator.py` itself stays untouched.
- **`reason` vs `clinical_reason`** → pass both through; the amber chip needs both strings.
- **`frequency`** → free string, preferred set `1-0-1` / `1-1-1` / OD / BD / TDS / QID /
  SOS / HS / stat. The schema enum in the spec is narrower than its own prompt rules.
- **`locked`** → included in the draft output, per §6, though `BUILD_TASK` omits it.
- **Bedrock** → verify model access in `ap-south-1` before writing the extractor.
  Model ID comes from `BEDROCK_MODEL_ID`, never hardcoded. Structured output via
  Converse API tool use, with a defensive parse.
- **Condition matching** → stays naive substring for the demo, despite `"gas"` and
  `"BP"` over-matching.

## Known data gaps

`verified_by` is empty on all 220 brands, 120 salts and 20 conditions — by the spec's
own Tier 0 definition nothing is verified yet. Referential integrity is clean; 5 salts
have no brand, 2 have no condition mapping.

## Build order

1. Extraction + glue layer ← **current**
2. Doctor dashboard
3. Twilio inbound + outbound
4. Step Functions pipeline
5. Reminders
6. Patient dashboard + history chat
7. Alexa (only if everything above is done)

Do first, regardless: verify Bedrock access in ap-south-1, send one WhatsApp message
end to end, submit the reminder template for approval. All three have external latency.
