VENV := .venv
PY   := $(CURDIR)/$(VENV)/bin/python

.PHONY: install test lint rebuild-seed clean

install:
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install -r backend/requirements.txt

test:
	cd backend && $(PY) -m pytest -q

lint:
	cd backend && $(PY) -m ruff check .

# The seed CSVs are the single source for the matcher index, the salt->indication
# map and hotwords.json. Edit a CSV and you must run this, or ASR and the matcher
# drift apart.
rebuild-seed:
	cd backend && $(PY) -m scripts.build_phonetic
	cd backend && $(PY) -m scripts.build_hotwords

clean:
	rm -rf $(VENV) backend/.pytest_cache backend/.ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
