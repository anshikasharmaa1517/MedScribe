# Build Task: Consultation Extraction Pipeline

## Context — what the overall product is

We are building **an ambient AI scribe for Indian clinics** (4-day hackathon build).

A doctor consults a patient normally, speaking Hindi/English/Hinglish. The system
listens, transcribes the conversation, and drafts a structured prescription on the
doctor's dashboard in near-real-time. The doctor reviews, edits and approves it.
On approval the prescription is sent to the patient over WhatsApp, along with
automatic medicine reminders.

The problem being solved: handwritten prescriptions in India are frequently
illegible and easily lost, patients have no durable medical record, and nobody
reminds them to take their medicines.

**Users:** doctor (web dashboard) and patient (WhatsApp + light web dashboard).

## Context — the architecture around this task

```
Browser mic
    ↓
Sarvam Saaras v3 (WebSocket streaming STT)     ← ALREADY WORKING, do not touch
    ↓
transcript text
    ↓
>>> THIS TASK: extraction + glue layer <<<
    ↓
resolver.py  (medicine name -> verified brand)  ← ALREADY WRITTEN, do not rewrite
    ↓
validator.py (clinical sanity checks)           ← ALREADY WRITTEN, do not rewrite
    ↓
structured JSON -> doctor dashboard (React)
    ↓
doctor approves -> Step Functions pipeline (PDF, WhatsApp, reminders)  ← LATER
```

**Build only the extraction + glue layer in this task.** Do not build the
dashboard, the WhatsApp integration, the PDF generation, or the reminder
scheduling yet.

---

## Files provided

| File | What it is | What you do with it |
|---|---|---|
| `resolver.py` | Resolves a spoken medicine name to a verified brand from our database. Handles ASR mangling ("azithril" → Azithral), phonetic matching, brand suffix parsing (Pan vs Pan-D), spoken numbers ("six fifty" → 650). Returns a status: `AUTO` / `CONFIRM` / `RESOLVE`. | **Import and call it. Do not rewrite it.** Entry point: `Resolver().resolve(spoken_string) -> dict` |
| `validator.py` | Checks resolved medicines against the stated diagnosis. Flags contradictions (e.g. a diuretic prescribed for diabetes), duplicate salts under different brands, and handles the "no diagnosis yet" case. | **Import and call it. Do not rewrite it.** Entry point: `Validator().validate(medicines_list, diagnosis_text) -> dict` |
| `seed_brands.csv` | 220 Indian brands: base name, modifier, strength, form, salt mapping, phonetic key, spoken aliases. | Read by `resolver.py` at import. Just make sure it's on the path. |
| `seed_salts.csv` | 120 generic salts: drug class, common strengths, max daily dose, which conditions they treat. | Read by `validator.py` at import. |
| `seed_conditions.csv` | 20 GP conditions: ICD-10 code, Hinglish synonyms ("sugar", "BP", "pet kharab"), expected drug classes. | Read by `validator.py` at import. |
| `hotwords.json` | 370 brand + salt names fed to Saaras so ASR recognises drug names. | Already wired into the STT layer. Included for reference only. |
| `build_phonetic.py` | Regenerates the `phonetic_key` column in `seed_brands.csv`. | Run only if `seed_brands.csv` is edited. |
| `build_hotwords.py` | Regenerates `hotwords.json` from the seed CSVs. | Run only if `seed_brands.csv` or `seed_salts.csv` is edited. |

**Dependencies:** `rapidfuzz`, `metaphone`, plus AWS SDK (`boto3`) for Bedrock.

**Note on paths:** `resolver.py` and `validator.py` currently open the CSVs with
hardcoded relative paths. Parameterise these so the modules work when deployed to
Lambda, but keep the same default behaviour.

**Note on seed data:** if anyone edits `seed_brands.csv`, both build scripts must
be re-run. Otherwise phonetic keys go stale and the STT hotword list drifts out of
sync with the matcher — the single-source setup exists to prevent exactly that.

---

## What to build

### Component 1 — Extractor

```python
def extract(transcript_text: str) -> dict
```

One **Amazon Bedrock** call (Claude Sonnet 4), structured JSON output, this exact
schema:

```json
{
  "symptoms": ["string"],
  "diagnosis": "string | null",
  "tests_advised": ["string"],
  "medicines": [
    {
      "spoken_name": "string, verbatim from transcript",
      "frequency": "1-0-1 | OD | BD | TDS | null",
      "food_relation": "before food | after food | null",
      "duration": "string | null"
    }
  ],
  "next_visit": "string | null"
}
```

**Critical prompt rules:**

1. **`spoken_name` must be copied VERBATIM from the transcript.** The model must
   NOT normalise, correct, spell-check, or expand drug names. If the transcript
   says "azithril", it returns "azithril". Correcting names is the resolver's job,
   and pre-correcting destroys the signal the matcher needs to detect errors.
2. The model must **never output a medicine ID or database identifier.** It only
   reports what was said.
3. **Fields may be empty.** Never invent a symptom or diagnosis to fill a slot. An
   empty `diagnosis` is a valid and common output.
4. The transcript is Hinglish — code-mixed Hindi and English. The prompt must
   handle "ek subah ek shaam" → `1-0-1`, "khaane ke baad" → `after food`,
   "paanch din" → `5 days`.
5. Indian dosage shorthand must be preserved: OD, BD, TDS, QID, SOS, HS, stat, and
   the 1-0-1 / 1-1-1 notation.

Set `max_tokens` conservatively and keep the prompt tight — this call runs every
10–15 seconds during a live consultation.

### Component 2 — Glue

```python
def process(transcript_text: str) -> dict
```

```python
ex = extract(transcript_text)
meds = [resolver.resolve(m["spoken_name"]) for m in ex["medicines"]]
# carry frequency / food_relation / duration across from ex["medicines"][i]
# onto the corresponding meds[i] dict, preserving index order
result = validator.validate(meds, ex["diagnosis"])
```

Return this to the dashboard:

```json
{
  "symptoms": ["..."],
  "diagnosis": "...",
  "conditions_matched": ["..."],
  "tests_advised": ["..."],
  "medicines": [
    {
      "spoken": "azithril 500",
      "brand_id": "B058",
      "matched": "Azithral 500mg",
      "salt_ids": ["S019"],
      "status": "AUTO | CONFIRM | RESOLVE",
      "clinical": "OK | UNKNOWN | CONTRADICTS | PENDING_CHECK",
      "reason": "...",
      "alternatives": [{"brand_id": "...", "label": "...", "score": 0}],
      "frequency": "1-0-1",
      "food_relation": "after food",
      "duration": "5 days"
    }
  ],
  "duplicate_salts": [["Paracetamol", ["Dolo 650mg", "Crocin 650mg"]]],
  "next_visit": "after 5 days"
}
```

### Component 3 — Execution rules

**When it runs:**
- **Live:** debounced, every 10–15 seconds, on the accumulated transcript so far.
  Re-extract the **whole transcript** each time, not incrementally — simpler and
  avoids drift.
- **On diagnosis change:** re-run validation. Doctors frequently state medicines
  before stating the diagnosis, so the clinical check must be re-evaluated when
  the diagnosis arrives.
- **At approval:** run once more on the final complete transcript. The live pass
  drives the screen; the approval pass is authoritative.

**Invariants that must not be broken:**
1. The LLM **never** sets `brand_id`. Only `resolver.py` does.
2. **Doctor edits win.** Once the doctor touches a field, mark it locked and stop
   overwriting it on subsequent extraction passes. (Without this, the next
   15-second cycle silently wipes their correction — this is the single most
   likely bug to break a live demo.)
3. **Nothing is ever silently dropped.** A `RESOLVE` item stays visible on screen
   and blocks approval until the doctor resolves it. A missing medicine is more
   dangerous than a flagged wrong one, because nobody can notice what isn't there.
4. **Idempotent.** Re-running on the same transcript must give the same result.
5. The extractor must fail safe: if the Bedrock call errors or returns malformed
   JSON, return the previous good state and log — never crash the consultation.

**Config:** put the resolver thresholds (`AUTO_SCORE`, `MIN_SCORE`, `MIN_MARGIN`)
and the extraction interval in a single config file, not scattered across modules.
These get tuned during demo rehearsal.

---

## Acceptance tests

Write these as actual tests. Sample transcript:

```
Doctor: Namaste, kya problem hai?
Patient: Sir, do din se bukhar hai aur body pain bhi hai.
Doctor: Temperature check karte hain... 101 hai. Viral fever lag raha hai.
        CBC karwa lena. Dolo 650 le lena, ek subah ek shaam, khaane ke baad,
        paanch din. Aur Cetzine 10 raat ko. Paanch din baad dikha dena.
```

Expected:
- `symptoms` contains fever and body pain
- `diagnosis` maps to Viral fever
- `tests_advised` contains CBC
- 2 medicines, both resolving `AUTO` with `clinical: OK`
- Dolo: `frequency 1-0-1`, `food_relation after food`, `duration 5 days`
- `next_visit` captured

Also test:
1. **ASR mangle** — transcript says "azithril 500" → resolves to Azithral 500mg
2. **Contradiction flag** — diagnosis "sugar diabetes" + "metrogyl 400" → status
   `CONFIRM`, clinical `CONTRADICTS`
3. **No diagnosis** — medicines stated, no diagnosis → clinical `PENDING_CHECK`,
   no flags raised
4. **Duplicate salt** — "dolo 650" and "crocin 650" → `duplicate_salts` populated
5. **Doctor edit persistence** — edit a field, re-run `process()`, confirm the edit
   survives
6. **Unresolvable name** — a nonsense drug name → status `RESOLVE`, blocks approval

---

## Do NOT build in this task

- The doctor dashboard / React UI
- WhatsApp integration
- PDF generation
- Reminder scheduling / EventBridge
- The Alexa skill
- Any change to the Saaras STT layer (already working)
- Any rewrite of `resolver.py` or `validator.py`
