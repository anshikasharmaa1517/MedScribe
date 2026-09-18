"""Pins resolver.py behaviour so threshold/config changes are made deliberately.

resolver.py is vendored. Anything that changes an expectation here must be an
intentional decision recorded in CLAUDE.md, not a side effect.
"""
import pytest

from core.resolver import Resolver


@pytest.fixture(scope="module")
def resolver():
    return Resolver()


@pytest.mark.parametrize(
    "spoken, status, brand_id",
    [
        ("dolo 650", "AUTO", "B001"),
        ("Dolo 650", "AUTO", "B001"),
        ("crocin 650", "AUTO", "B006"),
        ("azithril 500", "AUTO", "B058"),
        ("cetzine 10", "AUTO", "B025"),
        ("metrogyl 400", "AUTO", "B073"),
        ("pan d", "AUTO", "B086"),
        ("pan 40", "AUTO", "B085"),
        ("augmentin 625", "AUTO", "B053"),
        ("zifi 200", "AUTO", "B061"),
        ("crocine", "AUTO", "B006"),
        ("six fifty dolo", "CONFIRM", "B001"),
        ("amlo 5", "CONFIRM", "B130"),
        ("azithro", "CONFIRM", "B058"),
        ("dolomite", "CONFIRM", "B001"),
        ("zorblaxitron 900", "RESOLVE", None),
    ],
)
def test_pinned_resolution(resolver, spoken, status, brand_id):
    r = resolver.resolve(spoken)
    assert (r["status"], r["brand_id"]) == (status, brand_id)


@pytest.mark.parametrize(
    "spoken",
    [
        "xyzzy",        # nonsense; at MIN_SCORE=62 this was CONFIRM -> Xyzal 5mg (score 66)
        "paracetamol",  # a salt name; was CONFIRM -> Stamlo 5mg, a BP drug (score 65)
        "ranitidin",    # was CONFIRM -> Pantocid 40mg (score 65)
    ],
)
def test_low_confidence_guesses_become_resolve(resolver, spoken):
    """MIN_SCORE is 72 so a mid-60s guess is surfaced, not offered as a match.

    A wrong CONFIRM looks plausible on screen; a RESOLVE forces the doctor to
    look. These scores sit between the old and new floor, so this fails if
    either the threshold or the scoring drifts back.
    """
    r = resolver.resolve(spoken)
    assert r["status"] == "RESOLVE"
    assert r["brand_id"] is None


def test_resolve_output_shape(resolver):
    r = resolver.resolve("dolo 650")
    assert set(r) == {
        "spoken", "parsed", "status", "reason", "brand_id", "matched", "salt_ids", "alternatives"
    }
    assert r["spoken"] == "dolo 650"
    assert all({"brand_id", "label", "score"} <= set(a) for a in r["alternatives"])


def test_resolve_item_status_none_for_unresolved(resolver):
    r = resolver.resolve("zorblaxitron 900")
    assert r["brand_id"] is None and r["matched"] is None and r["salt_ids"] == []
