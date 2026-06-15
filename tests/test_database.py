"""Tests for database.py migrations and schema."""
import os
import sqlite3
import tempfile
import pytest

# Point to a temp DB so tests don't pollute jobs.db
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from database import init_db, get_connection, get_jobs_for_reminder


def test_cv_score_column_exists():
    """cv_score column must exist after init_db()."""
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(job_listings)")
    cols = [row["name"] for row in cursor.fetchall()]
    conn.close()
    assert "cv_score" in cols, f"cv_score column missing; found: {cols}"


def _seed_jobs(rows, prefix="seed"):
    """Insert test rows into job_listings. Each row is (role, relevance_score)."""
    conn = get_connection()
    cursor = conn.cursor()
    for i, (role, score) in enumerate(rows):
        cursor.execute(
            """INSERT OR IGNORE INTO job_listings
               (job_id, portal, company, role, relevance_score, hidden, date_found)
               VALUES (?, 'test', 'Test Co', ?, ?, 0, '2024-01-01')""",
            (f"{prefix}-{i}-{role}", role, score),
        )
    conn.commit()
    conn.close()


def test_get_jobs_for_reminder_single_keyword():
    """Single keyword matches jobs whose role contains that term."""
    init_db()
    _seed_jobs([("Product Manager SWK1", 80), ("Software Engineer SWK1", 70)], prefix="swk1")

    results = get_jobs_for_reminder("Product Manager SWK1", min_score=0, max_jobs=10)
    roles = [r["role"] for r in results]
    assert any("Product Manager SWK1" in r for r in roles), f"Expected Product Manager in {roles}"
    assert not any("Software Engineer SWK1" in r for r in roles)


def test_get_jobs_for_reminder_comma_separated_keywords():
    """Comma-separated keywords should match jobs that contain ANY of the terms (OR logic)."""
    init_db()
    _seed_jobs([
        ("Product Manager CSV2", 80),
        ("Program Manager CSV2", 75),
        ("Software Engineer CSV2", 70),
    ], prefix="csv2")

    results = get_jobs_for_reminder("Product Manager CSV2, Program Manager CSV2", min_score=0, max_jobs=10)
    roles = [r["role"] for r in results]
    assert any("Product Manager CSV2" in r for r in roles), f"Product Manager missing from {roles}"
    assert any("Program Manager CSV2" in r for r in roles), f"Program Manager missing from {roles}"
    assert not any("Software Engineer CSV2" in r for r in roles), f"Software Engineer should not appear in {roles}"


def test_get_jobs_for_reminder_comma_keywords_respect_min_score():
    """min_score filter still applies when using comma-separated keywords."""
    init_db()
    _seed_jobs([
        ("Product Manager MSK3", 80),
        ("Program Manager MSK3", 10),   # below threshold
    ], prefix="msk3")

    results = get_jobs_for_reminder("Product Manager MSK3, Program Manager MSK3", min_score=50, max_jobs=10)
    roles = [r["role"] for r in results]
    assert any("Product Manager MSK3" in r for r in roles)
    assert not any("Program Manager MSK3" in r for r in roles), "Low-score job should be filtered out"


def test_digest_dedup_is_per_user():
    """mark_sent_in_digest / recently_sent_job_ids scoped to a user must not
    leak across users: marking a job sent for user A must not suppress it for
    user B."""
    from database import (
        mark_sent_in_digest, recently_sent_job_ids, get_or_create_user,
    )
    init_db()
    _seed_jobs([("Dedup Role DDP", 90)], prefix="ddp")
    # Resolve the seeded job_id
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT job_id FROM job_listings WHERE role = 'Dedup Role DDP'")
    job_id = cur.fetchone()["job_id"]
    conn.close()

    uid_a = get_or_create_user("dedup-a@test.local", "A")
    uid_b = get_or_create_user("dedup-b@test.local", "B")

    mark_sent_in_digest([job_id], user_id=uid_a)

    assert recently_sent_job_ids([job_id], user_id=uid_a) == {job_id}
    assert recently_sent_job_ids([job_id], user_id=uid_b) == set(), \
        "job sent to user A must not be marked sent for user B"


def test_select_digest_jobs_backfills_when_all_sent():
    """When every qualified job was already sent, the digest must still fill up
    to top_n by backfilling recently-sent jobs — never return an empty digest."""
    from database import select_digest_jobs, mark_sent_in_digest, get_or_create_user
    init_db()
    _seed_jobs([("PM Backfill A", 90), ("PM Backfill B", 85), ("PM Backfill C", 80)], prefix="bf")
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT job_id, role FROM job_listings WHERE role LIKE 'PM Backfill%'")
    ids = {r["role"]: r["job_id"] for r in cur.fetchall()}
    conn.close()
    uid = get_or_create_user("backfill@test.local", "BF")
    qualified = [
        {"job_id": ids["PM Backfill A"], "relevance_score": 90},
        {"job_id": ids["PM Backfill B"], "relevance_score": 85},
        {"job_id": ids["PM Backfill C"], "relevance_score": 80},
    ]
    mark_sent_in_digest([j["job_id"] for j in qualified], user_id=uid)
    picked = select_digest_jobs(qualified, top_n=2, days=7, user_id=uid)
    assert len(picked) == 2, f"expected backfill to fill 2, got {len(picked)}"
    assert picked[0]["job_id"] == ids["PM Backfill A"], "highest score should lead"


def test_select_digest_jobs_prefers_fresh():
    """Fresh (not-recently-sent) jobs are preferred over recently-sent ones."""
    from database import select_digest_jobs, mark_sent_in_digest, get_or_create_user
    init_db()
    _seed_jobs([("PM Fresh A", 90), ("PM Fresh B", 85), ("PM Fresh C", 80)], prefix="fr")
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT job_id, role FROM job_listings WHERE role LIKE 'PM Fresh%'")
    ids = {r["role"]: r["job_id"] for r in cur.fetchall()}
    conn.close()
    uid = get_or_create_user("fresh@test.local", "FR")
    qualified = [
        {"job_id": ids["PM Fresh A"], "relevance_score": 90},
        {"job_id": ids["PM Fresh B"], "relevance_score": 85},
        {"job_id": ids["PM Fresh C"], "relevance_score": 80},
    ]
    # Mark only the top job sent; asking for 2 should skip A and return B, C.
    mark_sent_in_digest([ids["PM Fresh A"]], user_id=uid)
    picked = select_digest_jobs(qualified, top_n=2, days=7, user_id=uid)
    picked_ids = [j["job_id"] for j in picked]
    assert ids["PM Fresh A"] not in picked_ids, "recently-sent job should be deferred"
    assert picked_ids == [ids["PM Fresh B"], ids["PM Fresh C"]]


def test_purge_stale_demo_users():
    """purge_stale_demo_users removes only old demo-* users, leaving real and
    fresh demo users intact."""
    from datetime import datetime, timedelta
    from database import purge_stale_demo_users, get_or_create_user, get_user_by_email

    init_db()
    domain = "demo.local"
    old_uid = get_or_create_user(f"demo-old@{domain}", "Old Demo")
    fresh_uid = get_or_create_user(f"demo-fresh@{domain}", "Fresh Demo")
    real_uid = get_or_create_user("real-user@gmail.com", "Real")

    # Backdate the "old" demo user past the 24h cutoff.
    conn = get_connection()
    cur = conn.cursor()
    stale = (datetime.now() - timedelta(hours=48)).isoformat()
    cur.execute("UPDATE users SET created_at = ? WHERE id = ?", (stale, old_uid))
    conn.commit()
    conn.close()

    removed = purge_stale_demo_users(domain, hours=24)
    assert removed == 1
    assert get_user_by_email(f"demo-old@{domain}") is None
    assert get_user_by_email(f"demo-fresh@{domain}") is not None
    assert get_user_by_email("real-user@gmail.com") is not None
