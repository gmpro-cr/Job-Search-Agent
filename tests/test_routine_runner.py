"""Tests for routine_runner.run_routines (per-routine digest emails)."""
import os
import tempfile
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

import database as db


@pytest.fixture(autouse=True)
def _isolate():
    """run_routines processes ALL users, so each test starts from a clean slate."""
    db.init_db()
    conn = db.get_connection(); cur = conn.cursor()
    for t in ("user_reminders", "job_listings", "user_job_state"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit(); conn.close()
    yield


def _seed_job(job_id, role, location, score, when, for_user=None):
    conn = db.get_connection(); cur = conn.cursor()
    cur.execute(
        """INSERT OR IGNORE INTO job_listings
           (job_id, portal, company, role, location, relevance_score, hidden, date_found)
           VALUES (?, 't', 'Co', ?, ?, ?, 0, ?)""",
        (job_id, role, location, score, when),
    )
    conn.commit(); conn.close()
    # Routine matching is per-recipient: score the job against the user's CV.
    if for_user is not None:
        db.set_user_cv_score(for_user, job_id, score)


def _recorder():
    sent = []

    def send_fn(recipient, jobs, preferences, subject=None):
        sent.append({"recipient": recipient, "n": len(jobs), "subject": subject})
        return True

    send_fn.sent = sent
    return send_fn


def _routine(**kw):
    base = {"id": kw.get("id", "r1"), "name": "Fintech PM", "email": "me@test.local",
            "keyword": "Product Manager", "location": "", "min_score": 50,
            "max_jobs": 10, "enabled": True}
    base.update(kw)
    return base


def test_sends_per_routine_and_stamps_last_sent(monkeypatch):
    import routine_runner
    monkeypatch.setenv("GMAIL_ADDRESS", "bot@test.local")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    db.init_db()
    uid = db.get_or_create_user("rr-a@test.local", "A")
    db.save_user_reminders(uid, [_routine(email="me@test.local")])
    now = datetime.now().isoformat()
    _seed_job("rr-1", "Senior Product Manager", "Pune", 80, now, for_user=uid)
    _seed_job("rr-2", "Product Manager Lending", "Remote", 70, now, for_user=uid)

    send_fn = _recorder()
    summary = routine_runner.run_routines(send_fn=send_fn)

    assert summary["emails_sent"] == 1 and summary["jobs_sent"] == 2
    assert send_fn.sent[0]["recipient"] == "me@test.local"
    assert "Fintech PM" in send_fn.sent[0]["subject"]
    # last_sent stamped + persisted
    saved = db.get_user_reminders(uid)
    assert saved[0].get("last_sent")


def test_skips_disabled_and_recipientless(monkeypatch):
    import routine_runner
    monkeypatch.setenv("GMAIL_ADDRESS", "bot@test.local")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    db.init_db()
    uid = db.get_or_create_user("rr-b@test.local", "B")
    db.save_user_reminders(uid, [
        _routine(id="d1", enabled=False),
        _routine(id="d2", email=""),
    ])
    _seed_job("rr-3", "Product Manager", "Pune", 80, datetime.now().isoformat())
    send_fn = _recorder()
    summary = routine_runner.run_routines(send_fn=send_fn)
    assert summary["emails_sent"] == 0 and send_fn.sent == []


def test_dedup_after_last_sent(monkeypatch):
    import routine_runner
    monkeypatch.setenv("GMAIL_ADDRESS", "bot@test.local")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    db.init_db()
    uid = db.get_or_create_user("rr-c@test.local", "C")
    db.save_user_reminders(uid, [_routine(id="c1")])
    # job found 1 minute ago
    t0 = (datetime.now() - timedelta(minutes=1)).isoformat()
    _seed_job("rr-4", "Product Manager", "Pune", 80, t0, for_user=uid)
    send_fn = _recorder()
    # first run sends; stamps last_sent = now (after t0)
    routine_runner.run_routines(send_fn=send_fn, now=datetime.now().isoformat())
    # second run: nothing newer than last_sent -> no email
    s2 = routine_runner.run_routines(send_fn=send_fn, now=datetime.now().isoformat())
    assert s2["emails_sent"] == 0


def test_user_scope_and_ignore_last_sent(monkeypatch):
    import routine_runner
    monkeypatch.setenv("GMAIL_ADDRESS", "bot@test.local")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "pw")
    db.init_db()
    uid_a = db.get_or_create_user("rr-e@test.local", "E")
    # last_sent = now would normally dedup-skip on the cron path
    db.save_user_reminders(uid_a, [_routine(id="e1", email="me@test.local",
                                            last_sent=datetime.now().isoformat())])
    uid_b = db.get_or_create_user("rr-f@test.local", "F")
    db.save_user_reminders(uid_b, [_routine(id="f1", email="b@test.local")])
    _seed_job("rr-6", "Product Manager", "Pune", 80, datetime.now().isoformat(), for_user=uid_a)
    send_fn = _recorder()
    s = routine_runner.run_routines(send_fn=send_fn, user_id=uid_a, ignore_last_sent=True)
    assert s["emails_sent"] == 1 and s["users"] == 1          # force-sent despite last_sent
    assert all(x["recipient"] == "me@test.local" for x in send_fn.sent)  # only user A's routine


def test_no_gmail_creds_is_noop(monkeypatch):
    import routine_runner
    monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    db.init_db()
    uid = db.get_or_create_user("rr-d@test.local", "D")
    db.save_user_reminders(uid, [_routine(id="x1")])
    _seed_job("rr-5", "Product Manager", "Pune", 80, datetime.now().isoformat())
    send_fn = _recorder()
    summary = routine_runner.run_routines(send_fn=send_fn)
    assert summary["emails_sent"] == 0 and send_fn.sent == []
