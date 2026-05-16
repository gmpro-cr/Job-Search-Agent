"""
One-time migration: wrap existing single-user state into a "default user" row.

Reads:
  - user_preferences.json
  - cv_data.json
  - reminders.json
  - job_listings.{applied_status, applied_date, cv_score, user_notes,
                  follow_up_date, rejection_reason, hidden}

Writes:
  - users (one row for the default user)
  - user_preferences
  - user_cv_data
  - user_reminders
  - user_job_state (one row per job whose default-user state is non-trivial)

Defaults:
  - Email is read from the DEFAULT_USER_EMAIL env var, then the EMAIL_RECIPIENT
    env var, then user_preferences.json's "email" / "gmail_address", then
    finally "default@example.com" so the script never aborts.

Re-running is safe: get_or_create_user is idempotent, and per-row UPSERTs in
the user-scoped tables overwrite previous migration output cleanly.

Usage:
  python -m scripts.migrate_to_multiuser
  DEFAULT_USER_EMAIL=me@example.com python -m scripts.migrate_to_multiuser
"""

import json
import logging
import os
import sys

# Ensure the repo root is importable when run as a script.
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from database import (  # noqa: E402
    init_db, get_connection, get_or_create_user,
    save_user_preferences, save_user_cv_data, save_user_reminders,
    upsert_user_job_state,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("migrate")

PREFS_PATH = os.path.join(_BASE, "user_preferences.json")
CV_DATA_PATH = os.path.join(_BASE, "cv_data.json")
REMINDERS_PATH = os.path.join(_BASE, "reminders.json")


def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def _resolve_default_email(prefs):
    candidates = [
        os.environ.get("DEFAULT_USER_EMAIL"),
        os.environ.get("EMAIL_RECIPIENT"),
        (prefs or {}).get("email"),
        (prefs or {}).get("gmail_address"),
    ]
    for c in candidates:
        if c and isinstance(c, str) and "@" in c:
            return c.strip().lower()
    logger.warning("No email found — falling back to default@example.com. "
                   "Set DEFAULT_USER_EMAIL to override.")
    return "default@example.com"


def migrate():
    init_db()  # ensure schema exists

    prefs = _load_json(PREFS_PATH)
    cv_data = _load_json(CV_DATA_PATH)
    reminders = _load_json(REMINDERS_PATH) or []

    email = _resolve_default_email(prefs)
    uid = get_or_create_user(email, name=(prefs or {}).get("name"))
    logger.info("Default user: id=%s email=%s", uid, email)

    # Preferences
    if prefs:
        save_user_preferences(uid, prefs)
        logger.info("Migrated user_preferences.json")
    else:
        logger.info("No user_preferences.json to migrate")

    # CV data
    if cv_data:
        save_user_cv_data(uid, cv_data)
        logger.info("Migrated cv_data.json (%d skills)", len(cv_data.get("skills") or []))
    else:
        logger.info("No cv_data.json to migrate")

    # Reminders. Migrate ALL reminders to the default user when they have no
    # owner_email; otherwise route each to its own owner.
    by_owner: dict = {}
    for r in reminders:
        owner = (r.get("owner_email") or "").strip().lower()
        if not owner:
            owner = email
        by_owner.setdefault(owner, []).append(r)
    for owner_email, rems in by_owner.items():
        owner_uid = get_or_create_user(owner_email)
        save_user_reminders(owner_uid, rems)
        logger.info("Migrated %d reminders for %s (uid=%s)", len(rems), owner_email, owner_uid)
    if not reminders:
        logger.info("No reminders.json to migrate")

    # Per-job state for the default user. We only emit rows for jobs whose
    # legacy columns indicate a non-default state, to keep user_job_state slim.
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT job_id, applied_status, applied_date, cv_score, user_notes,
               follow_up_date, rejection_reason, hidden
        FROM job_listings
        WHERE COALESCE(applied_status, 0) <> 0
           OR COALESCE(cv_score, 0) <> 0
           OR (user_notes IS NOT NULL AND user_notes <> '')
           OR follow_up_date IS NOT NULL
           OR rejection_reason IS NOT NULL
           OR COALESCE(hidden, 0) <> 0
        """
    )
    rows = c.fetchall()
    conn.close()

    migrated = 0
    for r in rows:
        fields = {
            "applied_status": r["applied_status"] or 0,
            "cv_score": r["cv_score"] or 0,
            "hidden": r["hidden"] or 0,
        }
        if r["applied_date"]:
            fields["applied_date"] = r["applied_date"]
        if r["user_notes"]:
            fields["user_notes"] = r["user_notes"]
        if r["follow_up_date"]:
            fields["follow_up_date"] = r["follow_up_date"]
        if r["rejection_reason"]:
            fields["rejection_reason"] = r["rejection_reason"]
        upsert_user_job_state(uid, r["job_id"], **fields)
        migrated += 1
    logger.info("Migrated per-job state rows: %d", migrated)

    logger.info("DONE. Default user_id=%s, email=%s", uid, email)
    return uid


if __name__ == "__main__":
    migrate()
