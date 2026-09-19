# Consultation API contract (step 7 ↔ step 8)

The doctor dashboard (`frontend/doctor/`) is built against this. The Lambda
handlers (step 7) must implement exactly this. Change it here first, then both sides.

Base URL: the `ApiUrl` stack output, e.g. `https://xxxx.execute-api.ap-south-1.amazonaws.com/dev`.
Auth: `Authorization: Bearer <Cognito ID token>` from the **doctor** pool on every
route below. The doctorId comes from the token claims, never from the request.
All bodies are JSON. Errors: `{ "error": "<message>" }` with a 4xx/5xx status.

## Routes

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/patients` | — | `Patient[]` — the doctor's linked patients (waiting list) |
| `POST` | `/consults` | `{ "patientId" }` | `Consult` (status `LIVE`, empty draft) |
| `GET` | `/consults/{id}` | — | `Consult` |
| `POST` | `/consults/{id}/transcript` | `{ "text", "speaker": "doctor"\|"patient"\|null, "seq" }` | `{ "accepted": true, "seq" }` — appends; server debounces extraction (12 s) |
| `GET` | `/consults/{id}/draft` | — | `Draft` (§6 shape below) |
| `PATCH` | `/consults/{id}/draft` | `DraftPatch` | `Draft` — applies a doctor edit, sets `locked` |
| `POST` | `/consults/{id}/approve` | — | **202** `{ "status": "APPROVING", "rxId", "executionArn" }` — starts the Step Functions pipeline; **409** if `draft.blocks_approval`. Poll `GET /consults/{id}` until `status` is `APPROVED`, `APPROVAL_FAILED` (see `approvalError`) or back to `LIVE` (final pass found a RESOLVE item; `approvalBlockedBy`). |
| `GET` | `/consults/{id}/prescription` | — | `Prescription` — the approved rx plus `document: { format: "pdf"\|"html", url, expires_in }` (fresh 1 h presigned link); **404** until approved |
| `GET` | `/brands?q=<text>` | — | `{ "brand_id", "label", "salt_ids" }[]` (max 20) — for the RESOLVE picker |

`GET /consults/{id}/draft` is polled every 3 s by the dashboard until the
WebSocket lands (step 14). **Extraction runs inside that GET**: an append only marks
the consult dirty; the next poll after `EXTRACTION_INTERVAL_SECONDS` (12 s) runs the
pipeline and stores the draft, so one poll in ~four takes ~2 s longer. No queue needed.

Auth is a Cognito JWT authoriser on the HTTP API (`/health` excluded). The doctorId is
`custom:doctorId` from the token, falling back to `sub`. Mint a dev token with
`python -m scripts.cognito_doctor --pool <id> --client <id> --email ... --password ...`
and put it in `frontend/doctor/.env` as `VITE_ID_TOKEN`.

## Shapes

```ts
type Patient = { patientId: string; name: string; age?: number; sex?: "M"|"F";
                 phone: string; lastVisit?: string /* ISO */ };

type Consult = { consultId: string; patientId: string; doctorId: string;
                 status: "LIVE"|"APPROVING"|"APPROVED"|"APPROVAL_FAILED"|"CANCELLED";
                 rxId?: string; approvalError?: string; approvalBlockedBy?: string[];
                 createdAt: string; transcript: TranscriptLine[]; draft: Draft };

type TranscriptLine = { seq: number; speaker: "doctor"|"patient"|null; text: string; at: string };

type Status   = "AUTO" | "CONFIRM" | "RESOLVE";
type Clinical = "OK" | "UNKNOWN" | "CONTRADICTS" | "PENDING_CHECK";

type Medicine = {
  med_key: string;            // stable identity across passes - PATCH by this, never by index
  spoken: string;             // verbatim ASR text, never edited by the server
  brand_id: string | null;    // null while RESOLVE
  matched: string | null;     // "Azithral 500mg"
  salt_ids: string[];
  status: Status;
  clinical: Clinical;
  reason: string;             // resolver reason: "score 94, margin 12"
  clinical_reason: string;    // validator reason: "not expected for Type 2 diabetes"
  alternatives: { brand_id: string; label: string; score: number }[];
  frequency: string | null;   // "1-0-1" | "OD" | ...
  food_relation: string | null;
  duration: string | null;
  locked: boolean;            // doctor touched it - later extraction passes must not overwrite
  deleted: boolean;           // doctor removed it - stays deleted (tombstone)
};

type Draft = {
  symptoms: string[];
  diagnosis: string | null;
  conditions_matched: string[];
  tests_advised: string[];
  medicines: Medicine[];
  duplicate_salts: [string, string[]][];   // [["Paracetamol", ["Dolo 650mg","Crocin 650mg"]]]
  next_visit: string | null;
  locked_fields: ("symptoms"|"diagnosis"|"tests_advised"|"next_visit")[];
  blocks_approval: boolean;
  approval_blocked_by: string[];           // med_keys with status RESOLVE
  extraction_error?: string;               // present when the last pass failed; draft is the prior good state
  updatedAt: string;
};
```

This is `core/pipeline.py`'s output plus `updatedAt`. The server stores it as-is
and passes it back as `prior_state` on the next extraction pass.

## DraftPatch (doctor edits)

One of:

```ts
{ "field": "diagnosis" | "next_visit", "value": string | null }
{ "field": "symptoms" | "tests_advised", "value": string[] }
{ "medicine": { "med_key": string,
                "frequency"?: string|null, "food_relation"?: string|null, "duration"?: string|null } }
{ "medicine": { "med_key": string, "brand_id": string } }     // doctor resolved/changed the brand
{ "medicine": { "med_key": string, "accept_alternative": string /* brand_id */ } }
{ "medicine": { "med_key": string, "confirm": true } }         // accept a CONFIRM as-is -> status AUTO
{ "medicine": { "med_key": string, "deleted": true } }
{ "medicine": { "add": { "brand_id": string, "frequency"?, "food_relation"?, "duration"? } } }
```

Server rules for every patch (these are rules 3, 4 and 5 from CLAUDE.md):

- Set `locked: true` on the touched medicine, or add the field to `locked_fields`.
- Changing `brand_id` re-resolves `matched` / `salt_ids` from Tier 0 and sets
  `status: "AUTO"`; `spoken` is never changed.
- A patch never removes a medicine from the list; `deleted: true` is a tombstone.
- After any patch, re-run validation (`clinical`, `duplicate_salts`, `blocks_approval`)
  and return the full `Draft`.
