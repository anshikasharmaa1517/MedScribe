# MedScribe — Project Context for Claude Code

Paste this whole file as your opening context. Attach the files listed in
§9 to the same session.

---

## 1. What we are building

**MedScribe** — an ambient AI scribe for Indian clinics, built in 3 days for an
AWS hackathon.

A doctor consults a patient normally, speaking Hindi/English/Hinglish. The system
listens, transcribes the consultation, and drafts a structured prescription on the
doctor's dashboard in near-real-time. The doctor reviews, edits and approves it.
On approval the prescription is sent to the patient over WhatsApp, along with
automatic medicine reminders.

**The problem:** handwritten prescriptions in India are frequently illegible and
easily lost. Patients have no durable medical record and nobody reminds them to
take their medicines. In August 2025 the Punjab & Haryana High Court held that a
legible prescription is part of the right to health under Article 21.

**Users:**

| User | Interface | Core actions |
|---|---|---|
| Doctor | React web dashboard | Start consult, review draft, resolve flags, approve |
| Patient | WhatsApp + light web dashboard | Scan QR, receive prescription, get reminders, ask history |
| Clinical reviewer | Admin table | Approve/reject Tier 2 proposed medicines |

---

## 2. Hackathon constraints

- 3 days build, ~half day reserved for a 3-minute demo video. No live demo — the
  video is what judges see.
- Must use AWS services (grand prize is decided on the AWS-deployed track).
- Free-tier AWS account with credits. Everything should scale to zero.
- Twilio free tier: **100 WhatsApp messages total**. Budget them.
- Judging: idea/impact, AWS usage, learning, working execution, demo video.

**Scope in:** QR patient linking, live transcription, live draft extraction,
medicine resolution + clinical validation, doctor approval, prescription PDF,
WhatsApp delivery, generic alternatives, medicine reminders, patient history chat,
Alexa pull-based skill (stretch).

**Scope out (roadmap slide only):** appointment booking/queue, AI voice calls to
receptionists, medicine delivery integration, drug-drug interaction checking, lab
report OCR.

---

## 3. Architecture

```
Browser mic
    ↓
Sarvam Saaras v3 (WebSocket streaming STT)        ← WORKING, do not touch
    ↓
API Gateway WebSocket → Extract Lambda (Bedrock)
    ↓
resolver.py  (spoken name → verified brand id)     ← BUILT, do not rewrite
    ↓
validator.py (clinical sanity checks)              ← BUILT, do not rewrite
    ↓
Doctor dashboard (React) — live draft, green/amber/red chips
    ↓  doctor approves
Step Functions:
    FinalTranscript (Saaras batch, diarized)
    → FinalExtract (Bedrock)
    → ResolveValidate
    → gate on any unresolved item
    → RenderPDF (WeasyPrint → S3)
    → PersistRx (DynamoDB)
    → SendPrescription (Twilio, presigned PDF URL)
    → SendGenerics (separate message)
    → ScheduleReminders (EventBridge Scheduler)
```

**Two paths, different guarantees.** The live loop may be approximate; it drives a
screen a doctor is watching. The approval pipeline must be exact and reliable, so
it is deterministic Step Functions states, not agent reasoning.

---

## 4. Tech stack

| Layer | Choice |
|---|---|
| STT | Sarvam Saaras v3 — WebSocket (live), batch API (final, diarized) |
| LLM | Amazon Bedrock, Claude Sonnet 4 |
| Agent framework | AWS Strands Agents SDK (Python), post-approval only |
| Matching | rapidfuzz + Double Metaphone, in-memory in Lambda |
| Records | DynamoDB, single-table design |
| Objects | S3 — audio, transcripts, PDFs, seed CSVs |
| API | API Gateway HTTP + WebSocket, Lambda |
| Pipeline | Step Functions |
| Reminders | EventBridge Scheduler |
| Auth | Cognito, two user pools (doctors, patients) |
| Messaging | **Twilio WhatsApp sandbox** (100 free messages) |
| PDF | WeasyPrint (HTML→PDF), Lambda layer |
| Frontend | React + Amplify Hosting |
| Voice | Alexa Skills Kit + Lambda, pull-based (stretch) |
| IaC | AWS SAM |

**Regions:** everything in **ap-south-1 (Mumbai)**, except the Alexa Lambda which
must be **us-east-1** (ASK only triggers Lambda in us-east-1, eu-west-1,
us-west-2, ap-northeast-1).

⚠️ **Verify Bedrock model access in ap-south-1 first.** If Claude Sonnet 4 is not
directly available, use the APAC cross-region inference profile (`apac.` prefix)
rather than moving the stack.

**Secrets:** Sarvam API key, Twilio SID + auth token in Secrets Manager or SSM.
Never env vars in the repo — it may need to be public for judging.

---

## 5. The safety design (this is the core of the product)

### Two error classes

- **Garbled name** — ASR hears "Azithral" as "azithril". String matching catches it.
- **Valid but wrong drug** — ASR hears "amlodipine" as "amiloride". Both are real
  drugs; fuzzy matching confidently picks the wrong one. **Only a clinical
  cross-check against the diagnosis catches this.** These look-alike/sound-alike
  (LASA) pairs are a documented source of real medication errors.

### Trust tiers

| Tier | Contents | Auto-matched |
|---|---|---|
| 0 | Curated, human-verified medicines/salts/conditions | Yes |
| 1 | Doctor's personal shortlist (entries already in Tier 0) | Yes, fast path |
| 2 | Agent-proposed entries from web search | **No — invisible to matching** |

An agent may **propose**. Only a human reviewer promotes Tier 2 → Tier 0.
Repetition is not verification.

### Status contract

| Status | Condition | UI |
|---|---|---|
| `AUTO` | score ≥ 88 and margin ≥ 6 | Green, fills silently |
| `CONFIRM` | margin < 6, LASA pair, or contradicts diagnosis | Amber, one tap |
| `RESOLVE` | nothing above floor score | Red, blocks approval |

**The margin matters more than the top score.** If Amiloride scores 91 and
Amlodipine 89, the 2-point gap is the signal, not the 91.

### Clinical verdicts

| Verdict | Raises a flag |
|---|---|
| `OK` — salt treats the condition, or its class is expected | No |
| `UNKNOWN` — no indication data for this salt | **No** |
| `CONTRADICTS` — salt has indication data and none matches | Yes → downgrades to CONFIRM |
| `PENDING_CHECK` — no diagnosis stated yet | No |

**Unknown is not wrong.** Legitimate Indian prescribing includes combination
syrups and off-label use no indication table covers. Flagging those makes an
alert-fatigue machine — clinician override rates for drug-safety alerts run
49–96%, and one study found 92.9% override where only 7.3% of alerts were
clinically appropriate.

### Non-negotiable rules

1. The LLM **never** sets `brand_id`. Only `resolver.py` does.
2. The LLM returns `spoken_name` **verbatim** from the transcript — never
   normalised or corrected. Pre-correcting destroys the signal the matcher needs.
3. **Nothing is ever silently dropped.** A `RESOLVE` item stays on screen and
   blocks approval. A missing medicine is more dangerous than a flagged wrong one,
   because nobody can notice what isn't there.
4. **Doctor edits win.** Once a field is touched, set `locked: true` and stop
   overwriting it on later extraction passes. *(Without this the next 15-second
   cycle wipes their correction — the single most likely bug to break the demo.)*
5. The LLM verifier may **upgrade** an item to CONFIRM. It may never resolve a
   name, remove an item, or clear a deterministic flag.
6. No web-search result is ever written into Tier 0 or Tier 1.
7. Nothing reaches the patient without doctor approval.

---

## 6. Data contracts

### Extraction output (from Bedrock)

```json
{
  "symptoms": ["string"],
  "diagnosis": "string | null",
  "tests_advised": ["string"],
  "medicines": [
    {
      "spoken_name": "string, VERBATIM from transcript",
      "frequency": "1-0-1 | OD | BD | TDS | null",
      "food_relation": "before food | after food | null",
      "duration": "string | null"
    }
  ],
  "next_visit": "string | null"
}
```

Fields may be empty. Never invent a symptom or diagnosis to fill a slot.
Hinglish must be handled: "ek subah ek shaam" → `1-0-1`, "khaane ke baad" →
`after food`, "paanch din" → `5 days`.

### Draft state (to the dashboard)

```json
{
  "symptoms": ["fever", "body pain"],
  "diagnosis": "viral fever",
  "conditions_matched": ["Viral fever"],
  "tests_advised": ["CBC"],
  "medicines": [{
    "spoken": "azithril 500",
    "brand_id": "B058",
    "matched": "Azithral 500mg",
    "salt_ids": ["S019"],
    "status": "AUTO | CONFIRM | RESOLVE",
    "clinical": "OK | UNKNOWN | CONTRADICTS | PENDING_CHECK",
    "reason": "score 94, margin 12",
    "alternatives": [{"brand_id": "B059", "label": "Zithrox 500mg", "score": 82}],
    "frequency": "1-0-1",
    "food_relation": "after food",
    "duration": "5 days",
    "locked": false
  }],
  "duplicate_salts": [],
  "next_visit": "after 5 days"
}
```

### Prescription output

**Header** (doctor profile): name, degree, registration number.
**Patient block:** unique ID, name, date.
**Clinical body:** symptoms, diagnosis, tests advised, medicines.

Salt first in capitals, brand in brackets, **no price on the prescription**:

```
1. PARACETAMOL 650 mg  (Dolo 650)  — Tablet
   1-0-1 · after food · 5 days
```

List every salt with strength for combination drugs. In the WhatsApp message,
spell timing out plainly ("1 tablet morning, 1 tablet night"). Generic
alternatives and prices go in a **separate** message.

---

## 7. Data model

Single DynamoDB table `medscribe`:

| Entity | PK | SK |
|---|---|---|
| Doctor | `DOC#<doctorId>` | `PROFILE` |
| Patient | `PAT#<patientId>` | `PROFILE` |
| Phone lookup | `PHONE#<e164>` | `PATIENT` |
| Consultation | `PAT#<patientId>` | `CONSULT#<iso>#<consultId>` |
| Prescription | `PAT#<patientId>` | `RX#<iso>#<rxId>` |
| Reminder | `PAT#<patientId>` | `REM#<iso>#<remId>` |
| Doctor shortlist (Tier 1) | `DOC#<doctorId>` | `BRAND#<brandId>` |
| Review queue (Tier 2) | `QUEUE` | `PENDING#<iso>#<propId>` |

GSI1 (`doctorId` / `SK`) serves the doctor's patient list and today's consults.

Three reference tables loaded from the seed CSVs: `brands` (220), `salts` (120),
`conditions` (20). The resolver loads the brand index into Lambda memory at cold
start.

S3 layout:
```
s3://medscribe-<env>/
  audio/<consultId>.webm
  transcripts/<consultId>.json
  rx/<rxId>.pdf            ← presigned URL handed to Twilio
  seed/                    ← the three CSVs
```

---

## 8. WhatsApp specifics (Twilio sandbox)

- **Patients must join the sandbox first.** The QR encodes join + link in one
  message: `https://wa.me/<sandbox-number>?text=join%20<code>%20START%20DR123`
  One scan joins, opens the 24h window, links patient↔doctor, records consent.
- **24-hour window:** free-form messages only within 24h of the patient's last
  inbound. Prescription delivery right after the consult is inside it. **Reminders
  fired later are outside it and need an approved template — submit day one.**
- **Buttons need approved Content Templates** and aren't reliable in sandbox.
  Accept both a button payload and plain text (`TAKEN` or `1`).
- **Validate `X-Twilio-Signature`** on every inbound request. The webhook is
  unauthenticated by necessity.
- **Budget:** ~10 messages for setup, 15 for rehearsal, 40+ reserved for
  recording. One consult ≈ 3 messages. **Log to console during development.**

Reminders: EventBridge Scheduler, one schedule per medicine course, named
`rx-<rxId>-<brandId>-<slot>` so retries are idempotent. 1-0-1 → 09:00 and 21:00
local, shifted by food relation. End date from `duration`.

---

## 9. Files attached to this session

| File | What it is | What to do with it |
|---|---|---|
| `resolver.py` | Spoken medicine name → verified brand. Handles ASR mangling, phonetic matching, brand suffix parsing (Pan vs Pan-D), spoken numbers ("six fifty" → 650). Returns AUTO/CONFIRM/RESOLVE. | **Import and call. Do not rewrite.** `Resolver().resolve(str) -> dict` |
| `validator.py` | Checks resolved medicines against the diagnosis. Indication check, LASA pairs, duplicate salts, PENDING_CHECK handling. | **Import and call. Do not rewrite.** `Validator().validate(list, str) -> dict` |
| `seed_brands.csv` | 220 Indian brands: base name, modifier, strength, form, salt mapping, phonetic key, spoken aliases. | Read by `resolver.py` at import |
| `seed_salts.csv` | 120 salts: drug class, strengths, max daily dose, conditions treated. | Read by `validator.py` at import |
| `seed_conditions.csv` | 20 GP conditions: ICD-10, Hinglish synonyms ("sugar", "BP", "pet kharab"), expected drug classes. | Read by `validator.py` at import |
| `hotwords.json` | 370 brand + salt names for Saaras. | Already wired into STT. Reference only. |
| `build_phonetic.py` | Regenerates `phonetic_key` in seed_brands.csv. | Run only if seed_brands.csv is edited |
| `build_hotwords.py` | Regenerates hotwords.json from the seed CSVs. | Run only if seed CSVs are edited |
| `BUILD_TASK_extraction_layer.md` | Detailed spec for the extraction + glue layer. | The first component to build |

**Dependencies:** `rapidfuzz`, `metaphone`, `boto3`.

**Paths:** `resolver.py` and `validator.py` currently open the CSVs with hardcoded
relative paths. Parameterise for Lambda, keep the same defaults.

**Seed data invariant:** the CSVs are the single source for three derived
artifacts — the matcher index, the salt→indication map, and hotwords.json. Editing
a CSV means re-running both build scripts, or the ASR and the matcher drift apart.

---

## 10. Build order

1. **Extraction + glue layer** — see `BUILD_TASK_extraction_layer.md`
2. **Doctor dashboard** consuming the draft shape
3. **Twilio** inbound + outbound, end to end
4. **Step Functions** pipeline
5. **Reminders** via EventBridge Scheduler
6. **Patient dashboard** + history chat
7. **Alexa** — only if everything above is done

**Do first, regardless:** verify Bedrock model access in ap-south-1, send one
WhatsApp message through Twilio end to end, and submit the reminder template for
approval. All three have external latency that can't be compressed later.

---

## 11. Known open items

- `verified_by` is empty across all three seed CSVs. The brand→salt mappings,
  strengths and manufacturers were AI-generated and need human verification before
  they count as Tier 0. **A wrong seed row is ground truth the validator cannot
  catch.**
- Amiloride (S073) has no condition mapping, so it returns `UNKNOWN` rather than
  `CONTRADICTS` — this weakens the planned demo flag. Give it a condition it
  actually treats (oedema/heart failure as C021).
- Five salts have no brand; three condition classes have no salt; seven salt
  classes aren't listed against any condition.
- `resolver.py` `MIN_SCORE = 62` is arguably too low — a nonsense drug name
  ("blahmycin") returns CONFIRM with a suggestion rather than RESOLVE. Consider 72.
- Digit-by-digit speech ("DOLO six five zero") isn't parsed — collapses to `6`.

---

## 12. Compliance framing

- **Consent** captured at QR onboarding (recording + storage + WhatsApp delivery).
- **DPDP Act 2023** applies — explicit consent, data minimisation, synthetic data
  only for the demo.
- **CDSCO SaMD:** frame the product as *flagging inconsistencies for doctor
  review*, never as recommending treatment. This framing is legally load-bearing.
- **Log every override** (flag dismissed, suggestion accepted) with the
  prescription — audit trail and future training data.

---

## 13. Demo video (3 min) — build toward this

1. **0:00–0:20** — illegible prescription + the court ruling
2. **0:20–1:30** — live consult: QR scan, transcript, draft filling, **the amber
   LASA flag firing** ("Amiloride is a diuretic — unusual for hypertension. Did you
   mean Amlodipine?"), doctor approves
3. **1:30–2:15** — patient's phone: prescription on WhatsApp, generics message,
   reminder fires, "Taken" tapped; Alexa query
4. **2:15–2:40** — architecture diagram
5. **2:40–3:00** — what we learned + roadmap

Keep a running learning log from hour one — it's scored, and it writes the last
segment.
