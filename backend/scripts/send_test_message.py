"""Send exactly one WhatsApp message, to prove the Meta Cloud API path end to end.

Dry run unless --really is passed. Credentials come from the environment or
../.env (WA_PHONE_NUMBER_ID, WA_ACCESS_TOKEN). The recipient must be in the
test number's allowed list (Meta console -> API Setup -> "To").

Run from backend/:
    python -m scripts.send_test_message --to +91XXXXXXXXXX                       # dry run
    python -m scripts.send_test_message --to +91XXXXXXXXXX --really          # hello_world template
    python -m scripts.send_test_message --to +91XXXXXXXXXX --really --text "hi"   # 24 h window
    python -m scripts.send_test_message --to +91XXXXXXXXXX --really \
        --template medicine_reminder --lang en --param Asha --param "Dolo 650" --param "9 pm"
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

from core.messaging import MessagingError, send_document, send_template, send_text  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="E.164, e.g. +919876543210")
    parser.add_argument("--text", help="free-form text (only inside the 24 h window)")
    parser.add_argument("--template", default="hello_world", help="template name")
    parser.add_argument("--lang", default="en_US", help="template language code")
    parser.add_argument("--param", action="append", default=[], help="template body {{n}} value")
    parser.add_argument("--document", help="public/presigned URL of a PDF to send")
    parser.add_argument("--filename", default="prescription.pdf")
    parser.add_argument("--really", action="store_true", help="actually send (metered)")
    parser.add_argument("--no-meter", action="store_true",
                        help="skip the DynamoDB budget counter (no AWS creds needed)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    kw = {"dry_run": not args.really, "meter": (lambda: 0) if args.no_meter else None}
    try:
        if args.document:
            out = send_document(args.to, args.document, args.filename, **kw)
        elif args.text:
            out = send_text(args.to, args.text, **kw)
        else:
            out = send_template(args.to, args.template, args.param or None, args.lang, **kw)
    except MessagingError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
