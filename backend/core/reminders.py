"""Medicine reminder planning and the TAKEN handshake (spec step 12).

Timing comes from `frequency` and `food_relation`: 1-0-1 is 09:00 and 21:00
local, shifted 30 min later for "after food" and 30 min earlier for "before
food". Everything is Asia/Kolkata; DynamoDB stores UTC.

Schedules are named `rx-<rxId>-<brandId>-<HHMM>` so re-creating one is a
no-op, and a per-dose reminder row has a deterministic id for the same reason.
"""
import re
from datetime import UTC, datetime, timedelta, timezone

from core import messaging

IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
TZ_NAME = "Asia/Kolkata"

SLOT_HOURS = {
    "1-0-0": [9], "0-1-0": [14], "0-0-1": [21], "1-0-1": [9, 21], "1-1-1": [9, 14, 21],
    "1-1-0": [9, 14], "0-1-1": [14, 21], "OD": [9], "BD": [9, 21], "TDS": [9, 14, 21],
    "QID": [8, 12, 16, 21], "HS": [21],
}
FOOD_SHIFT_MIN = {"after food": 30, "before food": -30}
WINDOW_HOURS = 24
MAX_COURSE_DAYS = 90

TAKEN_WORDS = {"taken", "1", "done", "ho gaya", "le li", "le liya", "liya", "yes"}


def course_days(duration: str | None, default: int = 5) -> int:
    if not duration:
        return default
    d = duration.lower()
    num = re.search(r"\d+", d)
    n = int(num.group()) if num else default
    if "week" in d or "hafte" in d or "hafta" in d:
        n *= 7
    elif "month" in d or "mahine" in d or "mahina" in d:
        n *= 30
    return max(1, min(n, MAX_COURSE_DAYS))


def next_visit_date(text: str | None, start: datetime) -> datetime | None:
    """'5 days' / 'after 3 days' / '1 week' / '2 weeks' -> 09:00 local on that day."""
    if not text:
        return None
    days = course_days(text, default=0)
    if days <= 0:
        return None
    day = (start + timedelta(days=days)).astimezone(IST)
    return day.replace(hour=9, minute=0, second=0, microsecond=0)


def slot_key(brand_id: str | None, index: int) -> str:
    return brand_id or f"m{index}"


def slots_for(med: dict) -> list[tuple[int, int]]:
    """(hour, minute) local slots for one medicine, food-shifted."""
    hours = SLOT_HOURS.get(str(med.get("frequency") or ""))
    if not hours:
        return []
    shift = FOOD_SHIFT_MIN.get(med.get("food_relation") or "", 0)
    out = []
    for h in hours:
        total = h * 60 + shift
        out.append((total // 60, total % 60))
    return out


def course_schedules(rx: dict, start: datetime) -> list[dict]:
    """One recurring schedule per medicine x slot, with a cron in IST and an end date."""
    start_ist = start.astimezone(IST)
    schedules = []
    for i, med in enumerate(rx.get("medicines", [])):
        days = course_days(med.get("duration"))
        end = start_ist + timedelta(days=days)
        end = end.replace(hour=23, minute=59, second=0, microsecond=0)
        for hour, minute in slots_for(med):
            hhmm = f"{hour:02d}{minute:02d}"
            schedules.append({
                "name": f"rx-{rx['rxId']}-{slot_key(med.get('brand_id'), i)}-{hhmm}",
                "cron": f"cron({minute} {hour} * * ? *)",
                "timezone": TZ_NAME,
                "startAt": start_ist.replace(second=0, microsecond=0),
                "endAt": end,
                "input": {
                    "type": "dose", "rxId": rx["rxId"], "patientId": rx.get("patientId"),
                    "brand_id": med.get("brand_id"), "label": med.get("label"),
                    "slot": hhmm, "medIndex": i,
                },
            })
    nv = next_visit_date(rx.get("next_visit"), start)
    if nv and nv > start_ist:
        schedules.append({
            "name": f"rx-{rx['rxId']}-visit",
            "at": nv,
            "timezone": TZ_NAME,
            "input": {"type": "next_visit", "rxId": rx["rxId"], "patientId": rx.get("patientId"),
                      "next_visit": rx.get("next_visit")},
        })
    return schedules


def dose_rows(rx: dict, start: datetime) -> list[dict]:
    """Every planned dose as a reminder row (status SCHEDULED). Deterministic ids."""
    start_ist = start.astimezone(IST)
    rows = []
    for i, med in enumerate(rx.get("medicines", [])):
        slots = slots_for(med)
        if not slots:
            continue
        key = slot_key(med.get("brand_id"), i)
        for d in range(course_days(med.get("duration"))):
            day = (start_ist + timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
            for hour, minute in slots:
                due = day + timedelta(hours=hour, minutes=minute)
                if due <= start_ist:
                    continue
                rows.append({
                    "remId": f"rem-{rx['rxId']}-{key}-{due.strftime('%Y%m%d-%H%M')}",
                    "dueAt": to_utc_iso(due),
                    "rxId": rx["rxId"], "brand_id": med.get("brand_id"), "label": med.get("label"),
                    "slot": f"{hour:02d}{minute:02d}", "status": "SCHEDULED",
                    "text": dose_text(med),
                })
    return rows


def dose_text(med: dict) -> str:
    return (f"Time for {med.get('label')}: {messaging.plain_timing(med)}. "
            "Reply TAKEN once you've had it.")


def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def within_window(last_inbound_iso: str | None, now: datetime) -> bool:
    """Free-form WhatsApp is allowed only within 24 h of the patient's last message."""
    if not last_inbound_iso:
        return False
    last = datetime.strptime(last_inbound_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return now - last < timedelta(hours=WINDOW_HOURS)


def is_taken_reply(text: str | None, button_payload: str | None = None) -> bool:
    if button_payload and button_payload.strip().lower().startswith("taken"):
        return True
    return bool(text) and text.strip().lower().rstrip(".!") in TAKEN_WORDS


def record_taken(store, patient_id: str, now: datetime) -> dict | None:
    """Mark the most recent SENT reminder for this patient as taken. Returns it, or None."""
    sent = [r for r in store.list_reminders(patient_id) if r.get("status") == "SENT"]
    if not sent:
        return None
    latest = max(sent, key=lambda r: r.get("sentAt") or "")
    store.set_reminder_fields(patient_id, latest["dueAt"], latest["remId"],
                              status="TAKEN", takenAt=to_utc_iso(now))
    return {**latest, "status": "TAKEN"}
