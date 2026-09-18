# MedScribe

An ambient AI scribe for Indian clinics. A doctor consults in Hindi/English/Hinglish;
the system transcribes, drafts a structured prescription live on the doctor's
dashboard, and on approval delivers it over WhatsApp with medicine reminders.

The safety design is the product: the LLM only reports what it heard, verbatim.
Deterministic code — `core/resolver.py` and `core/validator.py` — decides what drug
that actually was and whether it makes clinical sense against the diagnosis.

See `docs/CLAUDE_CODE_CONTEXT.md` for the full spec and `CLAUDE.md` for house rules.

## Quick start

```sh
make install     # creates .venv, installs backend deps
make test        # pytest
make lint        # ruff
```

`make install` needs Python 3.11+.

## Layout

```
backend/
  core/          resolver.py, validator.py (vendored — do not rewrite), settings.py
  data/          the three seed CSVs + hotwords.json
  scripts/       build_phonetic.py, build_hotwords.py
  tests/
frontend/        React doctor + patient dashboards (not built yet)
infra/           AWS SAM (not built yet)
docs/            spec, build task
```

## Seed data

`backend/data/*.csv` is the single source for three derived artifacts: the matcher
index, the salt→indication map, and `hotwords.json`. After editing any CSV:

```sh
make rebuild-seed
```

Skipping this lets the ASR hotword list and the matcher drift apart.

## Configuration

Copy `.env.example` to `.env`. Seed paths default to `backend/data/` and are
overridable with `MEDSCRIBE_DATA_DIR` (or per-file `MEDSCRIBE_BRANDS_CSV`, etc.)
so the same modules run unchanged in Lambda.

## Running the modules directly

```sh
cd backend
../.venv/bin/python -m core.resolver    # resolver test harness
../.venv/bin/python -m core.validator   # validator scenarios
```
