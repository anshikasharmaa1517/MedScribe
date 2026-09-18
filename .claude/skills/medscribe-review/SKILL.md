---
name: medscribe-review
description: Review MedScribe backend changes against the project's seven clinical-safety invariants (LLM never sets brand_id, spoken_name verbatim, nothing silently dropped, doctor edits locked, verifier only upgrades, no web data in Tier 0/1, no patient send without approval). Use whenever the user asks to review, check, audit or sanity-check code in this repo, before a commit or push, or after building extractor/glue/pipeline code — even if they don't say "invariants". Prefer this over the generic /code-review for anything under backend/.
---

# MedScribe invariant review

A generic reviewer looks for bugs. This review looks for the specific ways this
codebase can hurt a patient: a medicine that vanishes from the draft, a doctor's
correction silently overwritten, a wrong brand chosen by the LLM instead of the
matcher. Those failures don't crash — they pass tests and then fail in a clinic.
Read the diff with that lens.

## Scope

Default: everything uncommitted — `git diff HEAD` plus untracked files. If the
tree is clean, say so and offer to review the last commit instead. If the user
names a path or commit, review that.

Only Python under `backend/` carries the invariants. Skip `.venv/`, seed CSVs
(unless a build script is also in the diff — then check both were regenerated),
and docs.

## The checks

For each invariant, the concrete code shapes that violate it. Grep for these
rather than reasoning abstractly — the violations are usually one line.

**1. Only `resolver.py` sets `brand_id`.**
Look in any LLM prompt, schema, or tool definition for `brand_id`, `salt_id`,
`salt_ids`, or a generic `id` field. Look for `brand_id` being assigned from
anything other than a `Resolver().resolve()` result. The LLM reports what was
said; the matcher decides what it was.

**2. `spoken_name` reaches the resolver verbatim.**
Between `extract()` output and `Resolver.resolve()`, any `.lower()`, `.strip()`,
`.title()`, `re.sub`, alias lookup, or spell-fix on `spoken_name` is a violation.
The matcher scores the raw ASR output; pre-cleaning hides the mangling it needs
to detect. Normalising a *copy* for `med_key` is fine — the original field must
survive into the output untouched. Also check the prompt still says verbatim.

**3. Nothing is silently dropped.**
Look for list comprehensions or `filter` that exclude medicines by `status` or
`clinical`. Look for `continue` / bare `except` inside a loop over medicines.
Look for a merge that builds the new list only from the latest extraction —
a medicine the LLM stops emitting must persist from `prior_state` (union with
tombstones; only an explicit doctor delete removes it). A `RESOLVE` item must be
in the output and must block approval.

**4. Doctor edits win.**
Any assignment to a medicine field from a new extraction pass must be guarded by
`if not med.get("locked")`. Locked state must reattach by `med_key`, never by
list index — index order shifts when a drug is added mid-list. Check that
`locked` is present in the output schema.

**5. The verifier only upgrades.**
If there is an LLM verification pass: it may set `status` to `CONFIRM`. It may
not set `AUTO`, change `brand_id`, remove an item, or clear `clinical`,
`reason`, or `duplicate_salts`. Any of those is a deterministic result being
overridden by a probabilistic one.

**6. No web-search result reaches Tier 0/1.**
Any code path that writes to `seed_*.csv` or the in-memory brands/salts/
conditions tables from a network response.

**7. Nothing reaches the patient without approval.**
Any Twilio / WhatsApp / PDF / reminder send must be gated on an explicit
approval state. Look for send calls reachable from the live extraction loop.

**Fail-safe.** `process()` must return `prior_state` (or an empty draft) on
`BedrockError` — never raise into the consultation. Check the except clause
actually returns rather than logging and falling through to a partial result.

**Vendored files.** Any hunk in `core/resolver.py` or `core/validator.py` beyond
the documented import/default edits in `CLAUDE.md` is a blocker unless a contract
test pinning the old behaviour is in the same diff.

## What to auto-fix vs report

Fix directly, then list under Fixed:
- `ruff check --fix` findings
- A hardcoded `backend/data/...` path where a `core.settings` constant exists
- A missing `"locked": False` default when building a new medicine dict

Report only — never fix:
- Anything under invariants 1–7 or fail-safe. These are behaviour changes and
  the author needs to see them, not discover them in a diff.

## Report format

```
## Blockers
- [Rule 3] backend/core/glue.py:42 — comprehension drops RESOLVE items before output
  → fix: keep all items; set `blocks_approval` instead of filtering

## Warnings
- [Fail-safe] backend/core/glue.py:18 — except logs but falls through to partial dict

## Fixed
- ruff: 3 import-order fixes in backend/core/extractor.py

## Clean
- Rules 1, 2, 4–7 — no violations in this diff
```

One line per finding: rule tag, `file:line`, what the code does, what it should
do. Say which rules came back clean so the author knows they were checked, not
skipped. If there are no blockers, say so in the first line.
