"""Reminder planning, EventBridge schedule specs, the fire handler and TAKEN."""
import os
from datetime import UTC, datetime, timedelta

import pytest
from moto import mock_aws

from core import reminders
from core import store as store_mod
from core.store import Store, create_tables
from scripts import seed_demo_data

START = datetime(2026, 9, 19, 8, 0, tzinfo=reminders.IST)   # 08:00 IST
RX = {"rxId": "r1", "patientId": "pat-demo-001", "doctorId": seed_demo_data.DOCTOR_ID,
      "next_visit": "5 days", "medicines": [
          {"brand_id": "B001", "label": "Dolo 650mg", "frequency": "1-0-1",
           "food_relation": "after food", "duration": "2 days"},
          {"brand_id": "B085", "label": "Pan 40mg", "frequency": "OD",
           "food_relation": "before food", "duration": "1 week"},
          {"brand_id": "B999", "label": "Odd", "frequency": None},
      ]}


def test_course_days_parses_hinglish_and_units():
    assert reminders.course_days("5 days") == 5
    assert reminders.course_days("paanch din") == 5          # no digits -> default
    assert reminders.course_days("2 weeks") == 14
    assert reminders.course_days("1 month") == 30
    assert reminders.course_days("2 hafte") == 14
    assert reminders.course_days(None) == 5
    assert reminders.course_days("500 days") == 90           # capped


def test_slots_follow_spec_timing_and_food_shift():
    assert reminders.slots_for({"frequency": "1-0-1"}) == [(9, 0), (21, 0)]
    assert reminders.slots_for({"frequency": "1-0-1", "food_relation": "after food"}) == [(9, 30), (21, 30)]
    assert reminders.slots_for({"frequency": "OD", "food_relation": "before food"}) == [(8, 30)]
    assert reminders.slots_for({"frequency": "TDS"}) == [(9, 0), (14, 0), (21, 0)]
    assert reminders.slots_for({"frequency": None}) == []
    assert reminders.slots_for({"frequency": "SOS"}) == []    # no fixed time -> no reminder


def test_course_schedules_one_per_medicine_slot_plus_visit():
    scheds = reminders.course_schedules(RX, START)
    names = [s["name"] for s in scheds]
    assert names == ["rx-r1-B001-0930", "rx-r1-B001-2130", "rx-r1-B085-0830", "rx-r1-visit"]
    dolo = scheds[0]
    assert dolo["cron"] == "cron(30 9 * * ? *)" and dolo["timezone"] == "Asia/Kolkata"
    assert dolo["endAt"].date() == (START + timedelta(days=2)).date()
    assert dolo["input"] == {"type": "dose", "rxId": "r1", "patientId": "pat-demo-001",
                             "brand_id": "B001", "label": "Dolo 650mg", "slot": "0930", "medIndex": 0}
    visit = scheds[-1]
    assert visit["at"].strftime("%Y-%m-%d %H:%M") == "2026-09-24 09:00"
    assert visit["input"]["type"] == "next_visit"


def test_dose_rows_are_deterministic_and_skip_the_past():
    rows = reminders.dose_rows(RX, START)
    dolo = [r for r in rows if r["brand_id"] == "B001"]
    pan = [r for r in rows if r["brand_id"] == "B085"]
    assert len(dolo) == 4 and len(pan) == 7
    assert dolo[0]["remId"] == "rem-r1-B001-20260919-0930"
    assert dolo[0]["dueAt"] == "2026-09-19T04:00:00Z"          # 09:30 IST
    assert pan[0]["remId"] == "rem-r1-B085-20260919-0830"
    assert len({r["remId"] for r in rows}) == len(rows)
    assert all(r["status"] == "SCHEDULED" for r in rows)
    assert "Reply TAKEN" in dolo[0]["text"]
    # a start after the morning slot drops today's morning dose
    later = reminders.dose_rows(RX, START.replace(hour=10))
    assert len([r for r in later if r["brand_id"] == "B001"]) == 3


def test_window_and_taken_detection():
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    assert reminders.within_window("2026-09-19T00:00:00Z", now)
    assert not reminders.within_window("2026-09-18T11:59:00Z", now)
    assert not reminders.within_window(None, now)
    for t in ("TAKEN", "taken.", "1", "Done", "ho gaya", "Le li"):
        assert reminders.is_taken_reply(t)
    assert not reminders.is_taken_reply("kal se pet dard hai")
    assert reminders.is_taken_reply(None, button_payload="TAKEN_rem-r1")


@pytest.fixture
def env(monkeypatch):
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    monkeypatch.setenv("SSM_PREFIX", "")
    with mock_aws():
        db = store_mod.dynamodb_resource(endpoint_url=None, region="ap-south-1")
        create_tables(db)
        st = Store(db)
        seed_demo_data.seed(st)
        from handlers import reminders as fire

        monkeypatch.setattr(fire, "_store", st)
        yield fire, st


def seed_rx(st, start):
    rx = st.put_prescription("pat-demo-001", seed_demo_data.DOCTOR_ID,
                             {**RX, "createdAt": reminders.to_utc_iso(start)})
    for r in reminders.dose_rows(rx, start):
        st.put_reminder("pat-demo-001", r)
    return rx


def test_fire_uses_template_outside_window_and_text_inside(env):
    fire, st = env
    seed_rx(st, START)
    event = {"type": "dose", "rxId": "r1", "patientId": "pat-demo-001", "brand_id": "B001",
             "label": "Dolo 650mg", "slot": "0930", "medIndex": 0}
    at_slot = START.replace(hour=9, minute=30)

    out = fire.fire_dose(event, at_slot)                       # no inbound yet -> template
    assert out["sent"] and out["path"] == "template"
    assert out["result"]["payload"]["type"] == "template"
    assert out["result"]["payload"]["template"]["name"] == "medicine_reminder"
    row = st.get_reminder("pat-demo-001", "2026-09-19T04:00:00Z", "rem-r1-B001-20260919-0930")
    assert row["status"] == "SENT" and row["sentAt"] and row["sendPath"] == "template"

    again = fire.fire_dose(event, at_slot)                     # scheduler retry -> no double send
    assert again["skipped"] == "already sent"

    st.touch_patient_inbound("pat-demo-001", reminders.to_utc_iso(at_slot))
    evening = {**event, "slot": "2130"}
    out2 = fire.fire_dose(evening, START.replace(hour=21, minute=30))
    assert out2["path"] == "text" and "Dolo 650mg" in out2["result"]["payload"]["text"]["body"]


def test_taken_marks_latest_sent_reminder(env):
    fire, st = env
    seed_rx(st, START)
    event = {"type": "dose", "rxId": "r1", "patientId": "pat-demo-001", "brand_id": "B001",
             "label": "Dolo 650mg", "slot": "0930", "medIndex": 0}
    fire.fire_dose(event, START.replace(hour=9, minute=30))

    assert reminders.record_taken(st, "pat-demo-002", START) is None
    taken = reminders.record_taken(st, "pat-demo-001", START.replace(hour=9, minute=40))
    assert taken["remId"] == "rem-r1-B001-20260919-0930"
    row = st.get_reminder("pat-demo-001", taken["dueAt"], taken["remId"])
    assert row["status"] == "TAKEN" and row["takenAt"] == "2026-09-19T04:10:00Z"
    assert reminders.record_taken(st, "pat-demo-001", START) is None   # nothing left SENT


def test_next_visit_fire_is_idempotent(env):
    fire, st = env
    seed_rx(st, START)
    event = {"type": "next_visit", "rxId": "r1", "patientId": "pat-demo-001", "next_visit": "5 days"}
    out = fire.handler(event, None)
    assert out["sent"] and "follow-up" in out["result"]["payload"]["template"]["components"][0]["parameters"][1]["text"]
    assert fire.handler(event, None)["skipped"] == "already sent"


def test_unknown_type_raises(env):
    fire, _ = env
    with pytest.raises(ValueError):
        fire.handler({"type": "nope"}, None)
