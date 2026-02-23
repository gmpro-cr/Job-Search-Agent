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
