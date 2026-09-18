"""Single source of truth for paths and runtime config.

Every value is overridable by environment variable so the same modules run
unchanged under pytest, from the CLI, and in Lambda — where the seed CSVs
either ship in the deployment package or are synced from S3 into /tmp.
"""
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MEDSCRIBE_DATA_DIR", BACKEND_DIR / "data"))

BRANDS_CSV = os.environ.get("MEDSCRIBE_BRANDS_CSV", str(DATA_DIR / "seed_brands.csv"))
SALTS_CSV = os.environ.get("MEDSCRIBE_SALTS_CSV", str(DATA_DIR / "seed_salts.csv"))
CONDITIONS_CSV = os.environ.get("MEDSCRIBE_CONDITIONS_CSV", str(DATA_DIR / "seed_conditions.csv"))
HOTWORDS_JSON = os.environ.get("MEDSCRIBE_HOTWORDS_JSON", str(DATA_DIR / "hotwords.json"))

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini")

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_ID = os.environ.get("GEMINI_MODEL_ID", "gemini-3.6-flash")
