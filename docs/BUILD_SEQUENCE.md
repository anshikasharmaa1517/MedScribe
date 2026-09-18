# MedScribe — Build Sequence for Claude Code

14 steps, each with the prompt to paste and the commit to make afterwards.
Do not skip ahead — each step assumes the previous one is committed and working.

**Ground rule for every step:** if Claude Code proposes rewriting `resolver.py` or
`validator.py`, stop it. Those are frozen.

**Filename note:** these prompts refer to `CLAUDE_CODE_CONTEXT.md`. If you rename
it to `CLAUDE.md` (which Claude Code loads automatically every session), do a
find-and-replace in this file too.

---

## Step 0 — Orientation (the first prompt after attaching the files)

> Read `CLAUDE_CODE_CONTEXT.md` and `BUILD_TASK_extraction_layer.md` in full, plus
> `resolver.py`, `validator.py` and the three seed CSVs.
>
> Do not write any code yet. Reply with:
> 1. A one-paragraph summary of what we are building, in your own words.
> 2. The seven non-negotiable rules from §5, listed back to me.
> 3. The exact function signatures of `Resolver.resolve()` and
>    `Validator.validate()`, and what each returns.
> 4. Anything in the spec that is ambiguous, contradictory, or that you would need
>    to decide yourself before building. List these as questions.
>
> Then propose a repo structure for the whole project — backend, frontend, IaC,
> tests — but do not create it yet.

**Why this step:** if the summary comes back wrong, everything downstream is
wrong. Fix the context file before writing a line of code.

```
git commit -m "docs: add project context and build spec"
```

---

## Step 1 — Repo skeleton and local dev loop

> Create the repo structure you proposed, with a working local dev loop:
>
> - Python backend under `backend/`, with `requirements.txt`
>   (rapidfuzz, metaphone, boto3, pytest)
> - The seed CSVs under `backend/data/`
> - `resolver.py` and `validator.py` under `backend/core/`, with their CSV paths
>   parameterised (default to `backend/data/`, overridable by env var) — do not
>   change any other logic in those two files
> - A `Makefile` or `justfile` with `install`, `test`, `lint`
> - `.gitignore`, `.env.example` (SARVAM_API_KEY, TWILIO_ACCOUNT_SID,
>   TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER, AWS_REGION, BEDROCK_MODEL_ID)
> - A smoke test that imports both modules and resolves "dolo 650" successfully
>
> Run the smoke test and show me it passing.

```
git commit -m "chore: scaffold repo, parameterise seed data paths, add smoke test"
```

---

## Step 2 — Bedrock connectivity spike

> Write `backend/core/bedrock_client.py` — a thin wrapper around the Bedrock
> Runtime `converse` API that:
>
> - reads the model id from env (`BEDROCK_MODEL_ID`)
> - takes a system prompt and a user message, returns parsed JSON
> - strips markdown code fences before parsing
> - on malformed JSON or an API error, raises a typed exception — never returns
>   partial garbage
> - has a configurable `max_tokens`, defaulting to 1500
>
> Then write a script `scripts/check_bedrock.py` that sends one trivial request
> and prints the response, so I can verify model access in ap-south-1 before we
> build on it.
>
> Remember: if Claude Sonnet 4 is not directly available in ap-south-1, we use the
> APAC cross-region inference profile (`apac.` prefix model id).

**Run `scripts/check_bedrock.py` yourself before committing.** This is the single
external dependency that can block the whole build.

```
git commit -m "feat: add Bedrock client wrapper and access check script"
```

---

## Step 3 — The extractor

> Build the extractor from `BUILD_TASK_extraction_layer.md` §Component 1:
> `backend/core/extractor.py`, exposing `extract(transcript_text: str) -> dict`.
>
> Critical prompt requirements, restated:
> - `spoken_name` must be copied VERBATIM from the transcript. The model must not
>   normalise, correct or expand drug names. "azithril" stays "azithril".
> - The model never outputs a brand id or any database identifier.
> - Fields may be empty. Never invent a symptom or diagnosis to fill a slot.
> - Hinglish: "ek subah ek shaam" → `1-0-1`, "khaane ke baad" → `after food`,
>   "paanch din" → `5 days`.
> - Preserve Indian dosage shorthand: OD, BD, TDS, QID, SOS, HS, stat, 1-0-1.
>
> Keep the prompt tight — it runs every 10–15 seconds during a live consult.
>
> Put the system prompt in its own file so I can iterate on it without touching
> code. Write unit tests using the sample Hinglish transcript in
> `BUILD_TASK_extraction_layer.md` (under Acceptance tests) and mock the Bedrock
> call.

```
git commit -m "feat: add transcript extractor with verbatim spoken_name contract"
```

---

## Step 4 — The glue layer

> Build `backend/core/pipeline.py`, exposing `process(transcript_text) -> dict`,
> per `BUILD_TASK_extraction_layer.md` §Component 2 and §Component 3.
>
> - extract → resolve each `spoken_name` → carry dosage fields across by index →
>   validate against the diagnosis
> - Output exactly the draft shape in `CLAUDE_CODE_CONTEXT.md` §6
> - Idempotent: same transcript in, same result out
> - Fails safe: on extraction error, return the previous good state and log
> - Thresholds and the extraction interval go in one config module, not scattered
>
> Write the six acceptance tests from `BUILD_TASK_extraction_layer.md` as real
> pytest tests. All six must pass before you tell me you're done.

```
git commit -m "feat: add extraction pipeline with resolve and validate wiring"
```

---

## Step 5 — Data model and DynamoDB access layer

> Build `backend/core/store.py` — the DynamoDB access layer for the single-table
> design in `CLAUDE_CODE_CONTEXT.md` §7.
>
> - One table `medscribe`, GSI1 on `doctorId`/`SK`
> - Typed helpers per entity: doctor, patient, phone lookup, consultation,
>   prescription, reminder, doctor shortlist, review queue
> - A `scripts/seed_reference_tables.py` that loads the three CSVs into their
>   reference tables
> - A `scripts/seed_demo_data.py` that creates one demo doctor (invent name,
>   degree, registration number, clinic) and three synthetic patients with prior
>   prescription history — we need history for the suggestions to have something
>   to show
>
> Use `moto` or a local DynamoDB for tests. Do not hit real AWS in tests.

```
git commit -m "feat: add DynamoDB single-table store and seeding scripts"
```

---

## Step 6 — SAM template and first deploy

> Write `template.yaml` — an AWS SAM template for ap-south-1 covering:
> DynamoDB table + GSI1, S3 bucket, two Cognito user pools, an HTTP API,
> and one placeholder Lambda behind `GET /health`.
>
> Secrets come from SSM Parameter Store, not environment variables in the
> template. Add a `samconfig.toml` with `ap-south-1` pinned.
>
> Then walk me through `sam build && sam deploy --guided`, and confirm `/health`
> responds.

**Deploy early even though there's almost nothing in it.** Finding an IAM or
region problem now costs minutes; finding it on day 3 costs the demo.

```
git commit -m "feat: add SAM template, deploy skeleton stack to ap-south-1"
```

---

## Step 7 — Consultation API

> Build these consultation endpoints as Lambda handlers wired into the SAM
> template. The draft shape they return is `CLAUDE_CODE_CONTEXT.md` §6:
>
> - `POST /consults`
> - `POST /consults/{id}/transcript` — appends a chunk, triggers debounced extraction
> - `GET /consults/{id}/draft`
> - `PATCH /consults/{id}/draft` — a doctor edit; sets `locked: true` on the
>   touched field
> - `POST /consults/{id}/approve` — for now, just marks the consult approved
>
> **The locking rule is the one that matters:** once `locked` is true on a field,
> no later extraction pass may overwrite it. Write a test that edits a field,
> re-runs `process()`, and asserts the edit survived.
>
> Cognito-authorised, doctor scope only.

```
git commit -m "feat: add consultation API with draft locking"
```

---

## Step 8 — Doctor dashboard

> Build the doctor dashboard: React + Vite under `frontend/doctor/`.
>
> Screens:
> 1. Waiting list — patients who scanned the QR
> 2. Consultation screen — live transcript on the left with Dr./Patient role tags
>    (tap to swap a mislabel), draft fields on the right
> 3. The medicine list is the important part:
>    - green `AUTO` — filled, quiet
>    - amber `CONFIRM` — shows the reason and the alternatives, one tap to accept
>    - red `RESOLVE` — searchable picker, **blocks the approve button**
> 4. Approve button, disabled while any red item remains
>
> Poll `GET /consults/{id}/draft` every 3 seconds for now — we add the WebSocket
> later. Any field the doctor edits sends a PATCH and shows as locked.
>
> Keep the styling plain and legible. This is what the demo video films, so
> clarity beats decoration.

```
git commit -m "feat: add doctor dashboard with draft review and flag resolution"
```

---

## Step 9 — Twilio WhatsApp, both directions

> Build the WhatsApp layer per `CLAUDE_CODE_CONTEXT.md` §8:
>
> - `POST /webhooks/twilio` — inbound, unauthenticated, **validates
>   `X-Twilio-Signature` before doing anything**, returns 200 immediately and
>   processes asynchronously
> - Body routing: `START <doctorId>` links the patient and records consent;
>   `TAKEN` or `1` marks the most recent pending reminder taken; anything else
>   goes to the history chat (stub for now)
> - `backend/core/messaging.py` — outbound send, with a `DRY_RUN` env flag that
>   logs instead of sending. **Default it to true.** We have 100 messages total.
> - `scripts/generate_qr.py` — produces the QR encoding
>   `https://wa.me/<sandbox-number>?text=join%20<code>%20START%20<doctorId>`
>
> Then walk me through sending exactly one real message end to end to verify.

```
git commit -m "feat: add Twilio WhatsApp webhook, sender with dry-run, QR generator"
```

---

## Step 10 — PDF rendering

> Build `backend/core/pdf.py` — renders a prescription to PDF with WeasyPrint on a
> Lambda layer, writes it to S3, returns a 24-hour presigned URL.
>
> Layout per `CLAUDE_CODE_CONTEXT.md` §6:
> - Header: doctor name, degree, registration number, clinic
> - Patient block: unique ID, name, date
> - Symptoms, diagnosis, tests advised
> - Medicines — **salt first in capitals**, brand in brackets, every salt listed
>   for combination drugs, dosage as `1-0-1 · after food · 5 days`
> - **No prices anywhere on the prescription**
> - A doctor-confirmed but unresolved medicine prints normally but carries an
>   internal `unverified` flag
>
> Keep the HTML template in its own file. Generate one sample PDF from the demo
> data so I can look at it.

```
git commit -m "feat: add prescription PDF rendering to S3 with presigned URL"
```

---

## Step 11 — Step Functions pipeline

> Build the post-approval state machine from `CLAUDE_CODE_CONTEXT.md` §3, wired into the SAM
> template, triggered by `POST /consults/{id}/approve`:
>
> `FinalTranscript` → `FinalExtract` → `ResolveValidate` → gate on any unresolved
> item → `RenderPDF` → `PersistRx` → `SendPrescription` → `SendGenerics` →
> `ScheduleReminders`
>
> - The gate fails the execution back to the dashboard if any item is `RESOLVE`
> - `SendGenerics` is a separate message: same-salt alternatives, indicative
>   prices, framed as "ask your pharmacist" — never on the prescription itself
> - Each state retries twice with backoff
> - `SendPrescription` and `ScheduleReminders` key off `rxId` so a retry cannot
>   double-send or double-schedule
> - Write the doctor's dismissed flags and accepted suggestions alongside the
>   prescription — that's the audit trail

```
git commit -m "feat: add post-approval Step Functions pipeline"
```

---

## Step 12 — Reminders

> Build the reminder layer:
>
> - `ScheduleReminders` creates one EventBridge schedule per medicine course,
>   named `rx-<rxId>-<brandId>-<slot>` for idempotency
> - Timing from `frequency` and `food_relation`: 1-0-1 → 09:00 and 21:00 local,
>   shifted by food relation. End date from `duration`, schedules self-expire
> - `next_visit` gets its own schedule
> - A fire handler that sends the reminder, writes `sentAt`, and accepts both a
>   button payload and a plain `TAKEN`/`1` reply, writing `takenAt`
> - Message text spells timing out plainly: "1 tablet morning, 1 tablet night"
>
> Reminders fired later than 24h after the patient's last inbound message need an
> approved Twilio template — build for that path, not free-form.

```
git commit -m "feat: add medicine reminders via EventBridge Scheduler"
```

---

## Step 13 — Patient dashboard and history chat

> Build `frontend/patient/` — a light React app:
> - History timeline: past consults, prescriptions, downloadable PDFs
> - "Ask my history" chat
>
> And `POST /patients/{id}/ask` — a Bedrock call over that patient's records.
> **Scope it by the caller's Cognito identity, never by a parameter the client
> sends.** Same endpoint serves the doctor's "ask about this patient" chat, scoped
> to patients linked to that doctor.
>
> Also build the reviewer screen: a table of Tier 2 pending entries with
> approve/reject. It can be plain — but it must exist, because it's the artifact
> that shows we don't auto-trust agent proposals.

```
git commit -m "feat: add patient dashboard, history chat, and Tier 2 review screen"
```

---

## Step 14 — WebSocket upgrade (only if time allows)

> Replace the 3-second polling in the doctor dashboard with the API Gateway
> WebSocket API: `transcript.append` client→server, `draft.update` and
> `draft.error` server→client.
>
> Keep the polling code behind a feature flag so we can fall back instantly if the
> WebSocket misbehaves during recording.

```
git commit -m "feat: replace draft polling with WebSocket push"
```

---

## Stretch — Alexa

> Build the Alexa skill: a pull-based intent, "what medicine do I take tonight",
> backed by a Lambda in **us-east-1** (ASK cannot trigger a Lambda in ap-south-1)
> reading the Mumbai DynamoDB cross-region.
>
> Separate SAM template. Do not wire it into the main stack.

```
git commit -m "feat: add pull-based Alexa skill for medicine lookup"
```

---

## Before you record

```
git commit -m "chore: seed demo data and rehearsal fixtures"
git tag demo-v1
```

Tag it. If something breaks during recording, you want a known-good commit to
return to in seconds.

---

## If you fall behind

Cut in this order: Alexa → WebSocket (keep polling) → patient dashboard →
generics message. Never cut the amber flag firing — it is the demo.
