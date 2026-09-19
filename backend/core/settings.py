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

# Resolver thresholds (0-100 fuzzy scores). Tuned during demo rehearsal.
AUTO_SCORE = int(os.environ.get("MEDSCRIBE_AUTO_SCORE", 88))  # top must beat this to auto-fill
MIN_SCORE = int(os.environ.get("MEDSCRIBE_MIN_SCORE", 72))  # below this: RESOLVE, don't guess
MIN_MARGIN = int(os.environ.get("MEDSCRIBE_MIN_MARGIN", 6))  # must beat 2nd by this, else CONFIRM

# Live loop: seconds between re-extractions of the accumulated transcript.
EXTRACTION_INTERVAL_SECONDS = int(os.environ.get("MEDSCRIBE_EXTRACTION_INTERVAL", 12))

# DynamoDB. One app table (single-table design, spec §7) plus three reference tables.
# DYNAMODB_ENDPOINT_URL points at DynamoDB Local for dev; unset in Lambda.
MEDSCRIBE_TABLE = os.environ.get("MEDSCRIBE_TABLE", "medscribe")
BRANDS_TABLE = os.environ.get("BRANDS_TABLE", "brands")
SALTS_TABLE = os.environ.get("SALTS_TABLE", "salts")
CONDITIONS_TABLE = os.environ.get("CONDITIONS_TABLE", "conditions")
DYNAMODB_ENDPOINT_URL = os.environ.get("DYNAMODB_ENDPOINT_URL") or None

# S3 bucket for rx PDFs and audio (stack output BucketName). Presigned URL lifetime.
MEDSCRIBE_BUCKET = os.environ.get("MEDSCRIBE_BUCKET", "")
PDF_URL_TTL_SECONDS = int(os.environ.get("PDF_URL_TTL_SECONDS", 24 * 3600))

# WhatsApp via the Meta Cloud API. DRY_RUN defaults ON: nothing is sent unless explicitly
# enabled. Real sends are metered in DynamoDB and refused past MESSAGING_BUDGET.
# The test number reaches 5 verified recipients.
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
MESSAGING_BUDGET = int(os.environ.get("MESSAGING_BUDGET", "500"))
WA_PROVIDER = os.environ.get("WA_PROVIDER", "meta")
WA_API_VERSION = os.environ.get("WA_API_VERSION", "v25.0")
WA_PHONE_NUMBER_ID = os.environ.get("WA_PHONE_NUMBER_ID", "")
WA_BUSINESS_ACCOUNT_ID = os.environ.get("WA_BUSINESS_ACCOUNT_ID", "")
WA_ACCESS_TOKEN = os.environ.get("WA_ACCESS_TOKEN", "")
WA_APP_SECRET = os.environ.get("WA_APP_SECRET", "")  # signs X-Hub-Signature-256 on webhooks
WA_VERIFY_TOKEN = os.environ.get("WA_VERIFY_TOKEN", "")  # our string, echoed in the GET handshake
WA_TEST_NUMBER = os.environ.get("WA_TEST_NUMBER", "")  # E.164 of the sender, for the QR

# Post-approval Step Functions state machine (stack output). Unset = approve persists inline.
STATE_MACHINE_ARN = os.environ.get("STATE_MACHINE_ARN", "")

# Reminders: EventBridge Scheduler targets the reminder Lambda via SCHEDULER_ROLE_ARN.
# Unset = ScheduleReminders writes the plan rows only (tests, first deploy).
REMINDER_FUNCTION_ARN = os.environ.get("REMINDER_FUNCTION_ARN", "")
SCHEDULER_ROLE_ARN = os.environ.get("SCHEDULER_ROLE_ARN", "")
SCHEDULE_GROUP = os.environ.get("SCHEDULE_GROUP", "default")
WA_REMINDER_TEMPLATE = os.environ.get("WA_REMINDER_TEMPLATE", "medicine_reminder")

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "mantle")

AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL_ID = os.environ.get("GEMINI_MODEL_ID", "gemini-3.6-flash")

# Bedrock Mantle: OpenAI-compatible endpoint with a bearer key (Bedrock console -> API keys).
BEDROCK_API_KEY = os.environ.get("BEDROCK_API_KEY", "")
BEDROCK_MANTLE_URL = os.environ.get("BEDROCK_MANTLE_URL", "https://bedrock-mantle.ap-south-1.api.aws/v1")
MANTLE_MODEL_ID = os.environ.get("MANTLE_MODEL_ID", "openai.gpt-oss-120b")

# Extraction call. The prompt lives in a text file so it can be tuned without a
# code change; the token cap is deliberately low because this runs every 10-15s.
EXTRACT_PROMPT_PATH = os.environ.get(
    "MEDSCRIBE_EXTRACT_PROMPT", str(BACKEND_DIR / "core" / "prompts" / "extract_system.txt")
)
EXTRACT_MAX_TOKENS = int(os.environ.get("MEDSCRIBE_EXTRACT_MAX_TOKENS", "1024"))
