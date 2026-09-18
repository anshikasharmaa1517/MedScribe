---
name: repo-workflow
description: Run MedScribe's install/test/lint/push loop without re-deriving the commands or reloading full project docs. Use whenever the task is "run tests", "lint", "check it works", "push this", or similar routine repo maintenance rather than a design/build decision.
---

# MedScribe repo workflow

Backend lives in `backend/`, venv at `.venv/` (repo root). Use the **Bash tool** (Git
Bash) with forward slashes — not PowerShell. Run from the repo root unless stated.

## Commands

- Install: `.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt`
  (`requirements.txt` is the Lambda runtime set only)
  (only if `.venv` is missing or requirements changed — don't reinstall speculatively)
- Test: `.venv/Scripts/python.exe -m pytest -q backend`
- Lint: `.venv/Scripts/python.exe -m ruff check backend` (add `--fix` for auto-fixes)
- Rebuild seed artifacts (only after editing a seed CSV), from `backend/`:
  `../.venv/Scripts/python.exe -m scripts.build_phonetic && ../.venv/Scripts/python.exe -m scripts.build_hotwords`
- Seed DynamoDB, from `backend/` (add `--endpoint-url http://localhost:8000` for DynamoDB
  Local, `--create` to create tables):
  `../.venv/Scripts/python.exe -m scripts.seed_reference_tables && ../.venv/Scripts/python.exe -m scripts.seed_demo_data`.
  Store tests use moto — never real AWS.
- Load `.env` for a one-off script (repo root): `export $(grep -E '^[A-Z_]+=' .env | xargs)`
- SAM (from `infra/`): `../.venv/Scripts/sam.exe build`, `../.venv/Scripts/sam.exe deploy`.
  Deploy creates real resources — hand the command to the user rather than running it.

## Push policy

Work directly on `main`, no branches, no PRs — push only when the user explicitly
asks. Don't push as a side effect of testing/linting.

Commit messages: short and clean, no Claude/AI attribution or co-author lines.

## Token discipline

- Don't re-run test/lint to "confirm" a state that hasn't changed since the last run.
- Don't edit `CLAUDE.md` or other docs for routine work — only for load-bearing
  decisions a future session would otherwise have to re-derive.
- Prefer running the exact command over re-deriving it from Makefile/README each time.
