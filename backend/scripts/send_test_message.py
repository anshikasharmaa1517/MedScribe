"""Send exactly one WhatsApp message, to prove the Twilio path end to end.

Dry run unless --really is passed. Credentials come from the environment or
../.env (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER). The
recipient must already have joined the sandbox from that phone.

Run from backend/:
    python -m scripts.send_test_message --to +91XXXXXXXXXX                # dry run, prints
    python -m scripts.send_test_message --to +91XXXXXXXXXX --really       # one real message
"""
import argparse
import logging
import os
import sys
from pathlib import Path


def _load_dotenv():
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

from core.messaging import MessagingError, send_whatsapp  # noqa: E402

DEFAULT_BODY = (
    "MedScribe test: this is the one end-to-end WhatsApp message. "
    "If you can read this, outbound delivery works."
)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="E.164, e.g. +919876543210")
    parser.add_argument("--body", default=DEFAULT_BODY)
    parser.add_argument("--really", action="store_true", help="actually send (counts against 100)")
    parser.add_argument("--no-meter", action="store_true",
                        help="skip the DynamoDB budget counter (no AWS creds needed)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    meter = (lambda: 0) if args.no_meter else None
    try:
        out = send_whatsapp(args.to, args.body, dry_run=not args.really, meter=meter)
    except MessagingError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
