from datetime import datetime, timedelta

import outcome_model as om
from scorer import score_job

CV = {"raw_text": "9 years in corporate credit.", "skills": ["Credit Appraisal"]}
PREFS = {"job_titles": ["Product Manager", "Product Owner"], "locations": ["Pune"],
         "scoring_profile": "bfsi_ai"}
NOW = datetime(2026, 10, 21)
OLD = (NOW - timedelta(days=30)).isoformat()

LEND = dict(role="Product Owner - Lending", location="Pune", remote_status="hybrid",
            portal="naukri", job_description="Experience in lending required. Loan origination.")
BANK = dict(role="Product Manager", location="Pune", remote_status="on-site",
            portal="linkedin", job_description="Digital banking product.")


def _ex(i, job, **kw):
    return dict(job_id=f"j{i}", job=dict(job, company=f"C{i}"), **kw)


def test_labels():
    assert om.labels({"status": 4}, NOW) == ((1, 1.0), (1, 1.0))
    # Rejected after a phone screen still counts as a conversion.
    assert om.labels({"status": 6, "max_status": 3}, NOW)[0] == (1, 1.0)
    assert om.labels({"status": 6}, NOW)[0] == (0, 1.0)
    assert om.labels({"status": 1, "applied_date": OLD}, NOW)[0] == (0, om.GHOSTED_WEIGHT)
    assert om.labels({"status": 1, "applied_date": NOW.isoformat()}, NOW)[0] is None
    assert om.labels({"status": 2}, NOW) == (None, (1, 1.0))
    assert om.labels({"status": 0, "hidden": 1}, NOW) == (None, (0, 1.0))


def test_empty_history_is_a_no_op():
    m = om.build([], CV, PREFS, now=NOW)
    assert m.fingerprint == ""
    assert m.adjust(LEND, {"track": "product"}) == []


def test_only_rejections_adjust_nothing():
    """No callbacks yet = no contrast: the target segment must not be dragged down."""
    ex = [_ex(i, LEND, status=6) for i in range(8)]
    m = om.build(ex, CV, PREFS, now=NOW)
    assert m.fingerprint
    _s, bd = score_job(dict(LEND, company="New"), CV, PREFS, outcome_model=m)
    assert bd["learned"] == 0


def test_learns_what_converts():
    ex = [_ex(i, LEND, status=4, max_status=4) for i in range(4)]
    ex += [_ex(10 + i, BANK, status=1, applied_date=OLD) for i in range(8)]
    m = om.build(ex, CV, PREFS, now=NOW)
    _s, lend_bd = score_job(dict(LEND, company="New"), CV, PREFS, outcome_model=m)
    _s, bank_bd = score_job(dict(BANK, company="New"), CV, PREFS, outcome_model=m)
    conv = lambda bd: sum(a[2] for a in bd["adjustments"] if a[0] == "conversion")
    assert conv(lend_bd) > 0 > conv(bank_bd)
    assert abs(conv(lend_bd)) <= om.CONVERSION_MAX
    ins = m.insights()["conversion"]
    assert ins["active"] and ins["helps"] and ins["hurts"]


def test_fingerprint_tracks_labels():
    a = om.build([_ex(1, LEND, status=4)], CV, PREFS, now=NOW).fingerprint
    b = om.build([_ex(1, LEND, status=6)], CV, PREFS, now=NOW).fingerprint
    assert a and b and a != b
