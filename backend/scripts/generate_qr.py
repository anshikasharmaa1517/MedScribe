"""Generate the patient onboarding QR for a doctor.

One scan sends "START <doctorId>" to the clinic's WhatsApp number: it opens the
24 h window, links patient to doctor and records consent.

Run from backend/ (reads WA_TEST_NUMBER from the environment or ../.env):
    python -m scripts.generate_qr --doctor-id doc-demo-meera
    python -m scripts.generate_qr --doctor-id doc-demo-meera --number +15551234567 --out qr.png
"""
import argparse
import os
import sys
import urllib.parse
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

from core.messaging import wa_id  # noqa: E402
from core.settings import WA_TEST_NUMBER  # noqa: E402


def onboarding_url(number: str, doctor_id: str) -> str:
    return f"https://wa.me/{wa_id(number)}?text={urllib.parse.quote(f'START {doctor_id}')}"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--doctor-id", required=True)
    parser.add_argument("--number", default=WA_TEST_NUMBER, help="clinic WhatsApp number, E.164")
    parser.add_argument("--out", help="PNG path; omit to print ASCII only")
    args = parser.parse_args(argv)

    if not wa_id(args.number or ""):
        print("number missing: set WA_TEST_NUMBER or pass --number", file=sys.stderr)
        return 1

    url = onboarding_url(args.number, args.doctor_id)
    print(url)

    try:
        import qrcode
    except ImportError:
        print("pip install qrcode[pil] for the QR image", file=sys.stderr)
        return 1

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    if args.out:
        qr.make_image(fill_color="black", back_color="white").save(args.out)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
