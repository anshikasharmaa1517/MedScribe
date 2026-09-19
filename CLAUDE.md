# MedScribe — house rules

Full spec: `docs/CLAUDE_CODE_CONTEXT.md`. Extraction brief (done): `docs/BUILD_TASK_extraction_layer.md`.
Infra: `infra/README.md` (deploy, secrets, seeding, **teardown checklist**).
Step-by-step plan: `docs/BUILD_SEQUENCE.md` — **at step 6** (deploy + `/health` check pending).

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
- **LLM provider** → `LLM_PROVIDER` in `core/settings.py` selects `mantle` (default),
  `gemini` or `bedrock`; all three expose `converse_json(system, user)` and raise the
  `LLMError` family from `core/llm.py`. Extractor and pipeline never know which one.
  - `mantle` = Bedrock's OpenAI-compatible endpoint (`bedrock-mantle.ap-south-1`,
    bearer key from Bedrock console → API keys). Model `openai.gpt-oss-120b`: the only
    one of five tested with zero rule violations on Hinglish transcripts, ~1.7 s/call.
    Data retention set to "none" at account level. **This is the production path.**
  - `bedrock` = Converse API via IAM. Blocked on this account
    (`ValidationException: Operation not allowed`, every model, every region — new-account
    gate; support case pending). Code is ready; flip the env var when it lifts.
  - `gemini` = Google AI Studio free tier, `gemini-3.6-flash` (2.5 is retired for new
    keys). Works, ~4.7 s/call. Fallback only; free tier may train on inputs.
- **Extraction prompt** → lives in `core/prompts/extract_system.txt`, not in code, so it
  can be tuned during rehearsal. `extractor.normalise()` strips any id the model emits
  and pins the schema; `spoken_name` passes through untouched.
- **Resolver quirk** → `alternatives` is `scored[1:4]`, so a `RESOLVE` item's top
  candidate is not in the output at all. Fix in the glue/dashboard layer, not resolver.
- **Condition matching** → stays naive substring for the demo, despite `"gas"` and
  `"BP"` over-matching.

## Known data gaps

`verified_by` is empty on all 220 brands, 120 salts and 20 conditions — by the spec's
own Tier 0 definition nothing is verified yet. Referential integrity is clean; 5 salts
have no brand, 2 have no condition mapping.

## What exists (as of 2026-09-19)

- `core/extractor.py` + `core/prompts/extract_system.txt` — `extract(transcript) -> dict`.
- `core/pipeline.py` — `process(transcript, prior_state=None) -> dict`; merge, locking,
  tombstones, `blocks_approval`, fail-safe to prior state on `LLMError`.
- `core/llm.py`, `core/mantle_client.py`, `core/gemini_client.py`, `core/bedrock_client.py`.
- `core/store.py` — single-table DynamoDB layer (§7) + three reference tables;
  `scripts/seed_reference_tables.py`, `scripts/seed_demo_data.py` (Dr. Meera Krishnan,
  `doc-demo-meera`; patients `pat-demo-001..003` with 8 prior prescriptions that pass the
  validator clean). Tests use moto — never real AWS.
- `infra/template.yaml` + `samconfig.toml` — table + GSI1, reference tables, S3, two
  Cognito pools, HTTP API with Cognito JWT authoriser, `/health`, consultation API,
  Twilio webhook. **Deployed** as stack `medscribe-dev`; tables seeded (Tier 0 + demo).
- `core/messaging.py`, `core/inbound.py`, `handlers/whatsapp_webhook.py`,
  `scripts/generate_qr.py`, `scripts/send_test_message.py` — see the WhatsApp section.
- 156 tests, all offline. `.claude/skills/medscribe-review` reviews diffs against the
  seven rules.

## WhatsApp — Meta Cloud API (Twilio dropped 2026-09-19)

- Twilio's new trial flow only sends its own canned templates (error 21654), so it was
  replaced with the Meta Cloud API test number. Twilio code is gone; it's in git history
  (`d2bde23`, `17c9124`) if ever needed.
- `core/messaging.py`: `send_text` / `send_template` / `send_document` via Graph
  `POST /v23.0/{WA_PHONE_NUMBER_ID}/messages`; `valid_signature` checks
  `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with `WA_APP_SECRET`).
  `send_whatsapp` is the plain-text alias the inbound router uses.
- `handlers/whatsapp_webhook.py`: GET = Meta verification handshake (`WA_VERIFY_TOKEN`);
  POST = signature check → 200 → async self-invoke → `core/inbound.py` routing
  (START / TAKEN / chat stub). Message ids are claimed in DynamoDB (`WAMSG#<id>`) so
  Meta's redeliveries are no-ops. Voice notes/images/docs arrive as `[audio]` etc. and go
  to the chat stub — never dropped.
- Secrets in SSM under `/medscribe/dev/`: `WA_ACCESS_TOKEN`, `WA_APP_SECRET`,
  `WA_VERIFY_TOKEN`, `WA_PHONE_NUMBER_ID`. Locally the same names in `.env`, plus
  `WA_TEST_NUMBER` for the QR (`scripts/generate_qr.py` → `wa.me/<number>?text=START <doctorId>`).
- Constraints: test number reaches **5 verified recipients**; free-form text/documents
  only inside 24 h of the patient's last inbound; templates otherwise. The
  `medicine_reminder` utility template ({{1}} name, {{2}} medicine, {{3}} timing) is
  submitted from WhatsApp Manager and is what step 12 sends via `send_template`.
- Outbound is dry-run by default (`MESSAGING_DRY_RUN`); real sends are metered in
  DynamoDB `METER#whatsapp_sent`, refused past `MESSAGING_BUDGET` (500).
- Patient `23989008536c` (+918899511700) was linked during the Twilio test and is still
  in the table.

## Build order

1. Extraction + glue layer ✅
2. Doctor dashboard ✅ (mock API; wiring to the real API in progress)
3. Twilio inbound + outbound ✅
4. Step Functions pipeline ← **next**
5. Reminders
6. Patient dashboard + history chat
7. Alexa (only if everything above is done)

Do first, regardless: get a permanent Meta system-user token (the API Setup one dies in
24 h) and submit the `medicine_reminder` template. (Bedrock: resolved via Mantle.)

## AWS account

Account `383688933731`, region `ap-south-1`, IAM user `anai` (has `AdministratorAccess`
only so `sam deploy` works — remove after). Hard budget **$100**; everything deployed
is on-demand/free-tier. **Tear everything down after results** — checklist in
`infra/README.md`.
