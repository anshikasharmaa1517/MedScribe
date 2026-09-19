"""Render a sample prescription from the demo data to out/sample_rx.{html,pdf}.

Run from backend/:
    python -m scripts.render_sample_pdf            # HTML always; PDF if WeasyPrint imports

WeasyPrint needs pango/cairo. On a machine without them, use Docker:
    docker run --rm -v "$PWD:/app" -w /app python:3.12-slim bash -c \\
      "apt-get update -qq && apt-get install -y -qq libpango-1.0-0 libpangoft2-1.0-0 \\
       fonts-dejavu-core > /dev/null && pip install -q weasyprint jinja2 && \\
       python -m scripts.render_sample_pdf"
"""
import sys
from pathlib import Path

from core.pdf import build_view, render_html
from core.reference import default_reference
from scripts.seed_demo_data import DOCTOR, PATIENTS

OUT = Path(__file__).resolve().parent.parent / "out"


def main():
    patient, visits = PATIENTS[1]
    _, visit = visits[1]
    rx = {**visit, "rxId": "sample-rx-001", "createdAt": "2026-09-19T10:30:00Z",
          "medicines": visit["medicines"] + [
              {"brand_id": None, "spoken": "zorblaxitron 900", "matched": None, "salt_ids": [],
               "med_key": "zorblaxitron 900", "frequency": "0-0-1", "duration": "3 days"},
          ]}
    view = build_view(rx, DOCTOR, patient, default_reference())
    html = render_html(view)

    OUT.mkdir(exist_ok=True)
    (OUT / "sample_rx.html").write_text(html, encoding="utf-8")
    print(f"wrote {OUT / 'sample_rx.html'}  (unverified: {view['unverified']})")

    try:
        from core.pdf import render_pdf
        data = render_pdf(html)
    except ImportError as e:
        print(f"PDF skipped - WeasyPrint not importable here ({e}). See docstring for Docker.")
        return 0
    (OUT / "sample_rx.pdf").write_bytes(data)
    print(f"wrote {OUT / 'sample_rx.pdf'}  ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
