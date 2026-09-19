"""Generate the patient onboarding QR for a doctor.

One scan sends "join <code> START <doctorId>" to the Twilio sandbox: it joins the
sandbox, opens the 24 h window, links patient to doctor and records consent.

Run from backend/ (reads TWILIO_WHATSAPP_NUMBER / TWILIO_SANDBOX_JOIN_CODE from
the environment or ../.env):
    python -m scripts.generate_qr --doctor-id doc-demo-meera
    python -m scripts.generate_qr --doctor-id doc-demo-meera --join-code happy-tiger --out qr.png
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

from core.settings import TWILIO_SANDBOX_JOIN_CODE, TWILIO_WHATSAPP_NUMBER  # noqa: E402


def onboarding_url(sandbox_number: str, join_code: str, doctor_id: str) -> str:
    digits = sandbox_number.replace("whatsapp:", "").lstrip("+")
    text = urllib.parse.quote(f"join {join_code} START {doctor_id}")
    return f"https://wa.me/{digits}?text={text}"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--doctor-id", required=True)
    parser.add_argument("--sandbox-number", default=TWILIO_WHATSAPP_NUMBER)
    parser.add_argument("--join-code", default=TWILIO_SANDBOX_JOIN_CODE)
    parser.add_argument("--out", help="PNG path; omit to print ASCII only")
    args = parser.parse_args(argv)

    if not args.join_code:
        print("join code missing: set TWILIO_SANDBOX_JOIN_CODE or --join-code", file=sys.stderr)
        return 1

    url = onboarding_url(args.sandbox_number, args.join_code, args.doctor_id)
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
