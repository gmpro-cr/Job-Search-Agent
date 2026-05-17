"""
database.py - DB operations for job listings tracking.

Dual driver: when DATABASE_URL points at Postgres (Neon / Vercel deploy) we
connect via psycopg3; otherwise we fall back to local SQLite. Call sites use
sqlite-style "?" placeholders — a thin cursor adapter rewrites them to "%s"
for Postgres so the rest of this file stays driver-agnostic.
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta

# Make sure .env-supplied DATABASE_URL is visible even when this module is
# imported before app.py (e.g. by the migration script).
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_IS_VERCEL = bool(os.environ.get("VERCEL"))
if _IS_VERCEL:
    DB_PATH = "/tmp/jobs.db"
else:
    DB_PATH = os.path.join(os.environ.get("DATA_DIR", _BASE_DIR), "jobs.db")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if USE_POSTGRES:
    import psycopg  # type: ignore
    from psycopg.rows import dict_row as _pg_dict_row  # type: ignore
    from psycopg import errors as _pg_errors  # type: ignore

    # Exceptions the rest of this module catches when probing schema state.
    _OPERATIONAL_ERRORS = (
        sqlite3.OperationalError,
        _pg_errors.UndefinedColumn,
        _pg_errors.DuplicateColumn,
        _pg_errors.DuplicateTable,
        _pg_errors.DuplicateObject,
        psycopg.errors.OperationalError,
        psycopg.Error,
    )
    _INTEGRITY_ERRORS = (sqlite3.IntegrityError, _pg_errors.UniqueViolation,
                         _pg_errors.IntegrityError)
else:
    _OPERATIONAL_ERRORS = (sqlite3.OperationalError,)
    _INTEGRITY_ERRORS = (sqlite3.IntegrityError,)


class _PgCursor:
    """Thin wrapper over a psycopg cursor that rewrites '?' placeholders
    to '%s' so the rest of the module can keep using sqlite-style SQL."""

    def __init__(self, real):
        self._c = real

    @staticmethod
    def _adapt(sql):
        # psycopg uses %s as the placeholder. Literal '%' characters in the
        # SQL (e.g. inside LIKE '%foo%') would otherwise be misread as
        # placeholders, so we double them to '%%' before rewriting '?' to
        # '%s'. Order matters: escape literals first so we don't damage the
        # placeholders we're about to insert.
        if "%" in sql:
            sql = sql.replace("%", "%%")
        if "?" in sql:
            sql = sql.replace("?", "%s")
        return sql

    def execute(self, sql, params=()):
        self._c.execute(self._adapt(sql), params)
        return self

    def executemany(self, sql, seq):
        self._c.executemany(self._adapt(sql), seq)
        return self

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def fetchmany(self, size):
        return self._c.fetchmany(size)

    @property
    def rowcount(self):
        return self._c.rowcount

    @property
    def description(self):
        return self._c.description

    def close(self):
        return self._c.close()

    def __iter__(self):
        return iter(self._c)


class _PgConn:
    """psycopg connection wrapper exposing the sqlite3.Connection surface
    used throughout database.py / app.py."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _PgCursor(self._conn.cursor())

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    # For compat with sqlite3.Connection assignments — no-op on Postgres
    # (we install dict_row at connect time).
    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, _):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        return False


class _RequestScopedProxy:
    """
    Lightweight wrapper that forwards every attribute to the underlying
    connection EXCEPT `.close()`, which becomes a no-op. The real close
    is handled once by `close_request_connection()` at request teardown.
    Lets every call site keep its `conn.close()` line without slamming
    a fresh TLS connection per query.
    """
    __slots__ = ("_real",)

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def close(self):
        return  # no-op; request teardown will close

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self._real.__enter__()

    def __exit__(self, *a, **k):
        return self._real.__exit__(*a, **k)


def _new_raw_connection():
    if USE_POSTGRES:
        pg = psycopg.connect(DATABASE_URL, row_factory=_pg_dict_row, autocommit=False)
        return _PgConn(pg)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_connection():
    """
    Return a DB connection. Picks psycopg/Postgres when DATABASE_URL is set,
    sqlite otherwise. Both flavours expose rows as dict-like objects.

    Inside a Flask request context, the same connection is reused across
    every call so a multi-query page (e.g. /dashboard with 6 stat queries)
    only pays one TLS handshake instead of six. The connection is closed
    automatically at request teardown (see close_request_connection).

    Outside a request (CLI scripts, migrations, the GitHub Actions scraper),
    behaves exactly as before — one owned connection per call.
    """
    try:
        from flask import g, has_app_context
    except Exception:
        return _new_raw_connection()
    if not has_app_context():
        return _new_raw_connection()
    cached = getattr(g, "_db_conn", None)
    if cached is None:
        cached = _new_raw_connection()
        g._db_conn = cached
    return _RequestScopedProxy(cached)


def close_request_connection(_exception=None):
    """Flask teardown_appcontext callback — closes the cached connection."""
    try:
        from flask import g, has_app_context
    except Exception:
        return
    if not has_app_context():
        return
    conn = getattr(g, "_db_conn", None)
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass
    g._db_conn = None


def _serial_pk():
    """Return the column-definition fragment for an auto-increment integer PK
    in whichever dialect we're targeting."""
    return "BIGSERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _add_columns_idempotent(conn, cursor, table, columns):
    """
    Add columns to a table idempotently across both drivers.
    Postgres supports ALTER TABLE ADD COLUMN IF NOT EXISTS; SQLite does not,
    so we catch its OperationalError per column.
    """
    for col in columns:
        if USE_POSTGRES:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col}")
            conn.commit()
        else:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                conn.commit()
            except sqlite3.OperationalError:
                pass


def init_db():
    """Create the job_listings table if it doesn't exist."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS job_listings (
            job_id TEXT PRIMARY KEY,
            portal TEXT NOT NULL,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            salary TEXT,
            salary_currency TEXT DEFAULT 'INR',
            location TEXT,
            job_description TEXT,
            apply_url TEXT,
            relevance_score INTEGER DEFAULT 0,
            remote_status TEXT DEFAULT 'on-site',
            company_type TEXT DEFAULT 'corporate',
            date_found TEXT NOT NULL,
            date_sent_in_digest TEXT,
            applied_status INTEGER DEFAULT 0,
            applied_date TEXT,
            user_notes TEXT
        )
    """)
    conn.commit()

    # Idempotent column additions. Postgres supports ADD COLUMN IF NOT
    # EXISTS; SQLite does not, so we fall back to a per-column try/except
    # there. We commit after each statement so a failure on one column
    # doesn't abort the rest of the DDL transaction.
    _extra_cols = [
        "date_posted TEXT",
        "poster_name TEXT", "poster_email TEXT", "poster_phone TEXT", "poster_linkedin TEXT",
        "experience_min INTEGER", "experience_max INTEGER",
        # BIGINT because some scrapers produce salary values above 2^31 (9-digit
        # rupee amounts when misparsed). SQLite treats INTEGER as 8 bytes so
        # this is backward-compatible there.
        "salary_min BIGINT", "salary_max BIGINT",
        "follow_up_date TEXT", "rejection_reason TEXT",
        "company_size TEXT", "company_funding_stage TEXT", "company_glassdoor_rating TEXT",
        "cv_score INTEGER DEFAULT 0",
        "hidden INTEGER DEFAULT 0",
    ]
    _add_columns_idempotent(conn, cursor, "job_listings", _extra_cols)

    # outreach_queue: stores LLM-drafted outreach emails pending human approval
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS outreach_queue (
            job_id           TEXT PRIMARY KEY,
            company          TEXT,
            role             TEXT,
            hiring_manager   TEXT,
            hm_email         TEXT,
            hm_linkedin      TEXT,
            email_draft      TEXT,
            linkedin_draft   TEXT,
            approval_token   TEXT UNIQUE,
            status           TEXT DEFAULT 'pending',
            llm_score        INTEGER,
            llm_reason       TEXT,
            created_at       TEXT,
            sent_at          TEXT,
            apply_url        TEXT
        )
    """)

    # Add hm_linkedin column to existing outreach_queue tables (idempotent)
    _add_columns_idempotent(conn, cursor, "outreach_queue", ["hm_linkedin TEXT"])

    # Phase 1: multi-user tables. job_listings remains shared (one scrape, all
    # users see). Per-user state (applied status, CV scores, notes, hidden,
    # preferences, CV) moves into user-scoped tables.
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            id          {_serial_pk()},
            email       TEXT UNIQUE NOT NULL,
            name        TEXT,
            picture     TEXT,
            created_at  TEXT NOT NULL,
            last_login  TEXT
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_job_state (
            user_id           INTEGER NOT NULL,
            job_id            TEXT    NOT NULL,
            applied_status    INTEGER DEFAULT 0,
            applied_date      TEXT,
            cv_score          INTEGER DEFAULT 0,
            user_notes        TEXT,
            follow_up_date    TEXT,
            rejection_reason  TEXT,
            hidden            INTEGER DEFAULT 0,
            updated_at        TEXT,
            PRIMARY KEY (user_id, job_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ujs_user_status ON user_job_state(user_id, applied_status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ujs_user_hidden ON user_job_state(user_id, hidden)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ujs_user_cv_score ON user_job_state(user_id, cv_score)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id     INTEGER PRIMARY KEY,
            prefs_json  TEXT NOT NULL,
            updated_at  TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_cv_data (
            user_id     INTEGER PRIMARY KEY,
            cv_json     TEXT NOT NULL,
            filename    TEXT,
            updated_at  TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS user_reminders (
            id              {_serial_pk()},
            user_id         INTEGER NOT NULL,
            reminder_id     TEXT NOT NULL,
            reminder_json   TEXT NOT NULL,
            updated_at      TEXT,
            UNIQUE(user_id, reminder_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_reminders_user ON user_reminders(user_id)")

    conn.commit()
    conn.close()
    logger.info("Database initialized at %s", DB_PATH)


def generate_job_id(portal, company, role, location):
    """Generate a unique job ID from portal + company + role + location."""
    import hashlib
    raw = f"{portal}:{company}:{role}:{location}".lower().strip()
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def job_exists(job_id):
    """Check if a job already exists in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM job_listings WHERE job_id = ?", (job_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def was_sent_recently(job_id, days=7):
    """Check if a job was already sent in a digest within the last N days."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cursor.execute(
        "SELECT 1 FROM job_listings WHERE job_id = ? AND date_sent_in_digest > ?",
        (job_id, cutoff),
    )
    sent = cursor.fetchone() is not None
    conn.close()
    return sent


def insert_job(job):
    """
    Insert a new job into the database. Returns True if inserted, False if duplicate.
    job: dict with keys matching the table columns.
    """
    job_id = job.get("job_id") or generate_job_id(
        job["portal"], job["company"], job["role"], job.get("location", "")
    )
    if job_exists(job_id):
        logger.debug("Job %s already exists, skipping insert", job_id)
        return False

    # Cross-portal dedup: same company + similar role from a different portal
    similar_id = find_similar_job(job["company"], job["role"], job.get("location", ""))
    if similar_id and similar_id != job_id:
        logger.debug(
            "Cross-portal duplicate detected: '%s' at '%s' (existing=%s)",
            job["role"], job["company"], similar_id,
        )
        return False

    # Normalize location at insert time
    raw_location = job.get("location")
    normalized_loc = normalize_location(raw_location) if raw_location else raw_location

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO job_listings
                (job_id, portal, company, role, salary, salary_currency, location,
                 job_description, apply_url, relevance_score, remote_status,
                 company_type, date_found, date_posted, applied_status,
                 experience_min, experience_max, salary_min, salary_max,
                 company_size, company_funding_stage, company_glassdoor_rating,
                 cv_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                    ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                job.get("portal", "unknown"),
                job["company"],
                job["role"],
                job.get("salary"),
                job.get("salary_currency", "INR"),
                normalized_loc,
                job.get("job_description"),
                job.get("apply_url"),
                job.get("relevance_score", 0),
                job.get("remote_status", "on-site"),
                job.get("company_type", "corporate"),
                datetime.now().isoformat(),
                job.get("date_posted"),
                job.get("experience_min"),
                job.get("experience_max"),
                job.get("salary_min"),
                job.get("salary_max"),
                job.get("company_size"),
                job.get("company_funding_stage"),
                job.get("company_glassdoor_rating"),
                job.get("cv_score", 0),
            ),
        )
        conn.commit()
        logger.debug("Inserted job %s: %s at %s", job_id, job["role"], job["company"])
        return True
    except _INTEGRITY_ERRORS:
        logger.debug("Duplicate job_id %s on insert", job_id)
        return False
    finally:
        conn.close()


def insert_jobs_bulk(jobs):
    """Insert multiple jobs, returning counts of inserted and skipped."""
    inserted = 0
    skipped = 0
    for job in jobs:
        if insert_job(job):
            inserted += 1
        else:
            skipped += 1
    return inserted, skipped


def mark_sent_in_digest(job_ids):
    """Mark jobs as sent in today's digest."""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    for jid in job_ids:
        cursor.execute(
            "UPDATE job_listings SET date_sent_in_digest = ? WHERE job_id = ?",
            (now, jid),
        )
    conn.commit()
    conn.close()
    logger.info("Marked %d jobs as sent in digest", len(job_ids))


def update_applied_status(job_id, status, notes=None, follow_up_date=None, rejection_reason=None):
    """
    Update applied status.
    0=New, 1=Applied, 2=Saved, 3=Phone Screen, 4=Interview, 5=Offer, 6=Rejected
    """
    conn = get_connection()
    cursor = conn.cursor()
    if status == 1 and not follow_up_date:
        cursor.execute(
            "UPDATE job_listings SET applied_status = ?, applied_date = ?, user_notes = ? WHERE job_id = ?",
            (status, datetime.now().isoformat(), notes, job_id),
        )
    elif status == 6 and rejection_reason:
        cursor.execute(
            "UPDATE job_listings SET applied_status = ?, rejection_reason = ?, user_notes = ? WHERE job_id = ?",
            (status, rejection_reason, notes, job_id),
        )
    else:
        sets = ["applied_status = ?", "user_notes = ?"]
        params = [status, notes]
        if follow_up_date:
            sets.append("follow_up_date = ?")
            params.append(follow_up_date)
        params.append(job_id)
        cursor.execute(
            f"UPDATE job_listings SET {', '.join(sets)} WHERE job_id = ?",
            params,
        )
    conn.commit()
    conn.close()


def get_unsent_jobs(min_score=65, limit=None):
    """Get jobs that haven't been sent in a digest yet, above the minimum score."""
    conn = get_connection()
    cursor = conn.cursor()
    query = """
        SELECT * FROM job_listings
        WHERE (date_sent_in_digest IS NULL)
        AND relevance_score >= ?
        ORDER BY relevance_score DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    cursor.execute(query, (min_score,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_jobs_found_today():
    """Get count of jobs found today."""
    conn = get_connection()
    cursor = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE date_found LIKE ?",
        (f"{today}%",),
    )
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_jobs_found_yesterday():
    """Get count of jobs found yesterday."""
    conn = get_connection()
    cursor = conn.cursor()
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE date_found LIKE ?",
        (f"{yesterday}%",),
    )
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_jobs_found_this_week():
    """Get count of jobs found in the last 7 days."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE date_found > ?", (cutoff,)
    )
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_portal_stats():
    """Get job count per portal."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT portal, COUNT(*) as cnt FROM job_listings GROUP BY portal ORDER BY cnt DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return {r["portal"]: r["cnt"] for r in rows}


def get_top_companies(limit=5):
    """Get top companies by number of job postings."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT company, COUNT(*) as cnt FROM job_listings GROUP BY company ORDER BY cnt DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [(r["company"], r["cnt"]) for r in rows]


def get_top_roles(limit=5):
    """Get top job titles by frequency."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, COUNT(*) as cnt FROM job_listings GROUP BY role ORDER BY cnt DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [(r["role"], r["cnt"]) for r in rows]


def get_comprehensive_stats():
    """Return a full stats dictionary for display."""
    return {
        "total_jobs": get_total_jobs(),
        "jobs_today": get_jobs_found_today(),
        "jobs_yesterday": get_jobs_found_yesterday(),
        "jobs_this_week": get_jobs_found_this_week(),
        "portal_stats": get_portal_stats(),
        "top_companies": get_top_companies(5),
        "top_roles": get_top_roles(5),
        "applied_count": get_applied_count(),
        "saved_count": get_saved_count(),
    }


def get_application_pipeline_stats():
    """Get counts for each application stage."""
    labels = {
        0: "New", 1: "Applied", 2: "Saved", 3: "Phone Screen",
        4: "Interview", 5: "Offer", 6: "Rejected",
    }
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT applied_status, COUNT(*) as cnt FROM job_listings GROUP BY applied_status"
    )
    rows = cursor.fetchall()
    conn.close()
    result = {label: 0 for label in labels.values()}
    for r in rows:
        label = labels.get(r["applied_status"], "New")
        result[label] = r["cnt"]
    return result


def get_best_matching_categories(limit=5):
    """Get role categories with highest average relevance scores."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            CASE
                WHEN LOWER(role) LIKE '%product manager%' OR LOWER(role) LIKE '%product lead%' THEN 'Product Management'
                WHEN LOWER(role) LIKE '%data%' OR LOWER(role) LIKE '%analytics%' THEN 'Data & Analytics'
                WHEN LOWER(role) LIKE '%program%' OR LOWER(role) LIKE '%project%' THEN 'Program/Project Management'
                WHEN LOWER(role) LIKE '%business%' OR LOWER(role) LIKE '%strategy%' THEN 'Business/Strategy'
                WHEN LOWER(role) LIKE '%design%' OR LOWER(role) LIKE '%ux%' THEN 'Design/UX'
                WHEN LOWER(role) LIKE '%engineer%' OR LOWER(role) LIKE '%developer%' THEN 'Engineering'
                WHEN LOWER(role) LIKE '%marketing%' OR LOWER(role) LIKE '%growth%' THEN 'Marketing/Growth'
                ELSE 'Other'
            END as category,
            COUNT(*) as total,
            ROUND(AVG(relevance_score), 1) as avg_score,
            SUM(CASE WHEN applied_status >= 1 THEN 1 ELSE 0 END) as applied
        FROM job_listings
        GROUP BY category
        ORDER BY avg_score DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_application_activity(days=30):
    """Get daily job counts for the last N days."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT DATE(date_found) as day,
               COUNT(*) as found,
               SUM(CASE WHEN relevance_score >= 65 THEN 1 ELSE 0 END) as high_score,
               SUM(CASE WHEN applied_status = 1 THEN 1 ELSE 0 END) as applied,
               SUM(CASE WHEN applied_status >= 1 THEN 1 ELSE 0 END) as acted_on
        FROM job_listings
        WHERE date_found >= ?
        GROUP BY DATE(date_found)
        ORDER BY day DESC
        LIMIT 14
    """, (cutoff,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recommended_actions():
    """Generate recommended next actions based on current data."""
    actions = []
    conn = get_connection()
    cursor = conn.cursor()

    # High-score jobs not yet applied
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE relevance_score >= 75 AND applied_status = 0"
    )
    high_score_new = cursor.fetchone()["cnt"]
    if high_score_new > 0:
        actions.append({
            "type": "action",
            "text": f"{high_score_new} high-scoring jobs (75+) you haven't acted on yet",
            "link": "/jobs?min_score=75&applied=none",
        })

    # Follow-ups due
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE follow_up_date IS NOT NULL AND follow_up_date <= ? AND applied_status NOT IN (5, 6)",
        (datetime.now().strftime("%Y-%m-%d"),)
    )
    follow_ups = cursor.fetchone()["cnt"]
    if follow_ups > 0:
        actions.append({
            "type": "reminder",
            "text": f"{follow_ups} application follow-ups are due today or overdue",
            "link": "/jobs?applied=applied",
        })

    # Saved but not applied
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE applied_status = 2"
    )
    saved = cursor.fetchone()["cnt"]
    if saved > 0:
        actions.append({
            "type": "info",
            "text": f"{saved} jobs saved for later - consider applying",
            "link": "/jobs?applied=saved",
        })

    # Jobs found today
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE date_found LIKE ? AND relevance_score >= 65",
        (f"{today}%",)
    )
    today_quality = cursor.fetchone()["cnt"]
    if today_quality > 0:
        actions.append({
            "type": "info",
            "text": f"{today_quality} quality jobs found today - review them",
            "link": "/jobs?min_score=65&sort=date_desc",
        })

    conn.close()
    return actions


def find_similar_job(company, role, location):
    """Check if a fuzzy-similar job already exists in the DB. Returns job_id or None."""
    from scrapers import _normalize_company_name, _fuzzy_role_match
    norm_company = _normalize_company_name(company)
    conn = get_connection()
    cursor = conn.cursor()
    # Fetch recent jobs to check against (limit scope for performance)
    cursor.execute(
        "SELECT job_id, company, role, location FROM job_listings ORDER BY date_found DESC LIMIT 2000"
    )
    rows = cursor.fetchall()
    conn.close()
    for r in rows:
        if _normalize_company_name(r["company"]) == norm_company:
            if _fuzzy_role_match(role, r["role"]):
                return r["job_id"]
    return None


def get_total_jobs():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM job_listings")
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_applied_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE applied_status = 1"
    )
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_saved_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE applied_status = 2"
    )
    count = cursor.fetchone()["cnt"]
    conn.close()
    return count


def get_portal_quality_stats():
    """Get average relevance score per portal - shows which portal returns best jobs."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT portal,
               COUNT(*) as total_jobs,
               ROUND(AVG(relevance_score), 1) as avg_score,
               MAX(relevance_score) as max_score,
               SUM(CASE WHEN relevance_score >= 65 THEN 1 ELSE 0 END) as quality_jobs
        FROM job_listings
        GROUP BY portal
        ORDER BY avg_score DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_job_contacts(job_id, poster_name, poster_email, poster_phone, poster_linkedin):
    """Update contact enrichment fields for a job listing."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE job_listings
           SET poster_name = ?, poster_email = ?, poster_phone = ?, poster_linkedin = ?
           WHERE job_id = ?""",
        (poster_name, poster_email, poster_phone, poster_linkedin, job_id),
    )
    conn.commit()
    conn.close()


def update_job_description(job_id, job_description):
    """Update the job_description field only if it is currently empty."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """UPDATE job_listings
           SET job_description = ?
           WHERE job_id = ? AND (job_description IS NULL OR job_description = '')""",
        (job_description, job_id),
    )
    conn.commit()
    conn.close()


def dedup_jobs():
    """
    Remove cross-portal / cross-session duplicates from the database.
    For each (company, role) pair, keeps the highest-scoring entry
    (earliest date_found as tiebreaker).  Returns number of rows deleted.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT job_id,
               LOWER(TRIM(company)) AS co,
               LOWER(TRIM(role))    AS ro,
               relevance_score,
               date_found
        FROM job_listings
        ORDER BY relevance_score DESC, date_found ASC
    """)
    rows = cursor.fetchall()

    seen = set()
    to_delete = []
    for row in rows:
        key = (row["co"], row["ro"])
        if key not in seen:
            seen.add(key)
        else:
            to_delete.append(row["job_id"])

    deleted = 0
    if to_delete:
        chunk_size = 500
        for i in range(0, len(to_delete), chunk_size):
            batch = to_delete[i : i + chunk_size]
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(
                f"DELETE FROM job_listings WHERE job_id IN ({placeholders})", batch
            )
            deleted += cursor.rowcount
        conn.commit()

    conn.close()
    logger.info("dedup_jobs: removed %d duplicate job listings", deleted)
    return deleted


def hide_job(job_id, hidden=True):
    """Mark a job as hidden (True) or visible again (False)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE job_listings SET hidden = ? WHERE job_id = ?",
        (1 if hidden else 0, job_id),
    )
    conn.commit()
    conn.close()


def update_job_notes(job_id, notes):
    """Update the user notes field for a job without touching other columns."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE job_listings SET user_notes = ? WHERE job_id = ?",
        (notes, job_id),
    )
    conn.commit()
    conn.close()


def get_distinct_locations():
    """Get sorted list of distinct non-null locations from job listings."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT location FROM job_listings WHERE location IS NOT NULL AND location != '' ORDER BY location"
    )
    rows = cursor.fetchall()
    conn.close()
    return [r["location"] for r in rows]


# ---------------------------------------------------------------------------
# Location normalization
# ---------------------------------------------------------------------------

# Map of canonical city name -> patterns that identify it
_CITY_PATTERNS = {
    "Pune": ["pune", "hinjewadi", "kharadi", "hadapsar", "baner", "wakad", "magarpatta"],
    "Mumbai": ["mumbai", "navi mumbai", "thane", "andheri", "bandra", "powai", "goregaon",
               "malad", "worli", "lower parel", "bkc", "airoli", "vashi"],
    "Bangalore": ["bengaluru", "bangalore", "whitefield", "koramangala", "indiranagar",
                  "electronic city", "marathahalli", "sarjapur", "bellandur", "hsr layout"],
    "Delhi / NCR": ["delhi", "noida", "gurgaon", "gurugram", "ghaziabad", "greater noida",
                    "faridabad", "manesar", "dwarka", "connaught place", "aerocity"],
    "Hyderabad": ["hyderabad", "secunderabad", "hitec city", "hitech city", "gachibowli",
                  "madhapur", "kondapur", "banjara hills"],
    "Chennai": ["chennai", "sholinganallur", "omr", "porur", "guindy", "tidel park"],
    "Kolkata": ["kolkata", "salt lake", "sector v", "rajarhat", "new town"],
    "Ahmedabad": ["ahmedabad", "gandhinagar", "gift city"],
    "Jaipur": ["jaipur"],
    "Chandigarh": ["chandigarh", "mohali", "panchkula"],
    "Kochi": ["kochi", "cochin", "infopark"],
    "Indore": ["indore"],
    "Coimbatore": ["coimbatore"],
    "Thiruvananthapuram": ["thiruvananthapuram", "trivandrum", "technopark"],
    # International
    "Singapore": ["singapore"],
    "Dubai / UAE": ["dubai", "abu dhabi", "uae", "united arab emirates"],
    "London": ["london", "uk", "united kingdom"],
    "US - Remote": ["united states", "usa"],
    "Remote": ["remote", "work from home", "wfh", "anywhere"],
    "India": ["india"],
}


# Canonical names from _CITY_PATTERNS that are clearly outside India/Remote.
# Used by _build_jobs_query to hide international noise by default.
_INTERNATIONAL_CANONICALS = {"London", "US - Remote", "Singapore", "Dubai / UAE"}

# Raw-string fragments that indicate a non-India location when a job's location
# wasn't normalized to a canonical name (e.g. "Cincinnati, OH").
_INTERNATIONAL_KEYWORDS = [
    "cincinnati", "chicago", "ohio", "new york", "los angeles",
    "san francisco", "seattle", "toronto", "sydney", "melbourne",
    "united states", " usa", " uk ", "united kingdom", "germany",
    "france", "paris", "amsterdam", "netherlands", "canada",
    "australia", "new zealand",
]


def normalize_location(raw_location):
    """
    Normalize a raw location string to a canonical city name.
    Returns the canonical name or the original string if no match.
    """
    if not raw_location:
        return ""
    raw_lower = raw_location.lower()
    for canonical, patterns in _CITY_PATTERNS.items():
        for pattern in patterns:
            if pattern in raw_lower:
                return canonical
    return raw_location


def get_normalized_locations():
    """
    Get sorted list of canonical (normalized) location names
    with counts, for the filter dropdown.
    Returns list of (canonical_name, count) tuples.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT location FROM job_listings WHERE location IS NOT NULL AND location != ''"
    )
    rows = cursor.fetchall()
    conn.close()

    from collections import Counter
    counts = Counter()
    for r in rows:
        canonical = normalize_location(r["location"])
        counts[canonical] += 1

    # Sort by count descending so most popular cities appear first
    return sorted(counts.items(), key=lambda x: -x[1])


def delete_old_jobs(days: int = 30) -> int:
    """
    Delete job listings older than `days` days.
    Returns the number of rows deleted.
    """
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM job_listings WHERE date_found < ?",
        (cutoff,),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    logger.info("delete_old_jobs: removed %d jobs older than %d days", deleted, days)
    return deleted


def get_jobs_for_reminder(keyword: str, min_score: int, max_jobs: int, since: str = None,
                          max_age_days: int = 3) -> list:
    """
    Return up to max_jobs listings whose role contains any of the comma-separated
    terms in `keyword` (case-insensitive, OR logic) and whose relevance_score >=
    min_score, ordered newest first. Only returns non-hidden jobs.

    since: ISO datetime string (last_sent). Jobs must be found after this timestamp.
    max_age_days: Hard recency cap — never return jobs older than this many days,
                  regardless of since. Defaults to 3 days so reminders only surface
                  fresh listings.
    """
    terms = [t.strip().lower() for t in keyword.split(",") if t.strip()]
    if not terms:
        return []
    role_clauses = " OR ".join("LOWER(role) LIKE ?" for _ in terms)
    params = [f"%{t}%" for t in terms] + [int(min_score)]

    # Recency cap: whichever cutoff is MORE recent wins
    recency_cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
    effective_since = recency_cutoff
    if since and since > recency_cutoff:
        effective_since = since

    params.append(effective_since)
    params.append(int(max_jobs))

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT * FROM job_listings
        WHERE ({role_clauses})
          AND relevance_score >= ?
          AND date_found > ?
          AND (hidden = 0 OR hidden IS NULL)
        ORDER BY date_found DESC
        LIMIT ?
        """,
        params,
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_skill_frequency(job_titles: list, limit: int = 50) -> list:
    """
    Return skills appearing most frequently across jobs matching the given titles.
    Returns list of dicts: {skill, count, pct} sorted by count desc.
    """
    conn = get_connection()
    cursor = conn.cursor()

    if job_titles:
        like_clauses = " OR ".join("LOWER(role) LIKE ?" for _ in job_titles)
        params = [f"%{t.lower()}%" for t in job_titles]
        cursor.execute(
            f"SELECT job_description, role FROM job_listings WHERE ({like_clauses}) AND (hidden = 0 OR hidden IS NULL)",
            params,
        )
    else:
        cursor.execute("SELECT job_description, role FROM job_listings WHERE (hidden = 0 OR hidden IS NULL)")

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return []

    total_jobs = len(rows)
    skill_counts: dict = {}

    from analyzer import extract_skills
    for row in rows:
        text = " ".join([row["role"] or "", row["job_description"] or ""])
        skills = extract_skills(text, max_skills=None)
        for skill in skills:
            skill_counts[skill] = skill_counts.get(skill, 0) + 1

    sorted_skills = sorted(skill_counts.items(), key=lambda x: -x[1])[:limit]
    return [
        {"skill": skill, "count": count, "pct": round(count / total_jobs * 100, 1)}
        for skill, count in sorted_skills
    ]


def get_keyword_frequency(job_titles: list, top_n: int = 30) -> list:
    """
    Return most frequent meaningful words in job descriptions for the given titles.
    Returns list of dicts: {word, count} sorted desc.
    """
    import re as _re
    from collections import Counter

    STOP_WORDS = {
        'the','and','for','with','our','you','your','will','this','that','are',
        'from','have','has','been','was','were','they','their','which','about',
        'into','more','also','than','can','all','any','its','not','but','who',
        'what','how','when','work','team','role','job','experience','skills',
        'looking','strong','ability','must','good','years','year','working',
        'based','across','including','company','position','candidate','knowledge',
        'manage','ensure','provide','support','help','using','use','used',
        'new','key','high','well','large','great','multiple','other','join',
    }

    conn = get_connection()
    cursor = conn.cursor()

    if job_titles:
        like_clauses = " OR ".join("LOWER(role) LIKE ?" for _ in job_titles)
        params = [f"%{t.lower()}%" for t in job_titles]
        cursor.execute(
            f"SELECT job_description FROM job_listings WHERE ({like_clauses}) AND (hidden = 0 OR hidden IS NULL)",
            params,
        )
    else:
        cursor.execute("SELECT job_description FROM job_listings WHERE (hidden = 0 OR hidden IS NULL)")

    rows = cursor.fetchall()
    conn.close()

    counter: Counter = Counter()
    for row in rows:
        text = (row["job_description"] or "").lower()
        words = _re.findall(r'\b[a-z]{4,}\b', text)
        for w in words:
            if w not in STOP_WORDS:
                counter[w] += 1

    return [{"word": w, "count": c} for w, c in counter.most_common(top_n)]


def get_dashboard_insights() -> dict:
    """
    Compute insight metrics for dashboard cards.
    Returns dict with: unopened_high_score, overdue_followups,
    avg_cv_score_this_week, avg_cv_score_last_week, top_missing_skill.
    """
    from datetime import datetime, timedelta
    conn = get_connection()
    cursor = conn.cursor()

    # Unopened high-score jobs (score >= 75, status = New/0)
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE relevance_score >= 75 AND applied_status = 0 AND (hidden=0 OR hidden IS NULL)"
    )
    unopened = cursor.fetchone()["cnt"]

    # Overdue follow-ups
    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM job_listings WHERE follow_up_date IS NOT NULL AND follow_up_date <= ? AND applied_status NOT IN (5,6)",
        (today,)
    )
    row = cursor.fetchone()
    overdue = row["cnt"] if row else 0

    # Avg CV score this week vs last week
    week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    prev_week_start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT ROUND(AVG(cv_score),1) as avg FROM job_listings WHERE date_found >= ? AND cv_score > 0",
        (week_start,)
    )
    r = cursor.fetchone()
    avg_cv_this_week = r["avg"] if r and r["avg"] else 0

    cursor.execute(
        "SELECT ROUND(AVG(cv_score),1) as avg FROM job_listings WHERE date_found >= ? AND date_found < ? AND cv_score > 0",
        (prev_week_start, week_start)
    )
    r = cursor.fetchone()
    avg_cv_last_week = r["avg"] if r and r["avg"] else 0

    # Top missing skill: most frequent skill in top unapplied jobs
    cursor.execute(
        "SELECT job_description FROM job_listings WHERE applied_status = 0 AND (hidden=0 OR hidden IS NULL) ORDER BY relevance_score DESC LIMIT 100"
    )
    jd_rows = cursor.fetchall()
    conn.close()

    from analyzer import extract_skills
    skill_counts: dict = {}
    for jd_row in jd_rows:
        for skill in extract_skills(jd_row["job_description"] or "", max_skills=None):
            skill_counts[skill] = skill_counts.get(skill, 0) + 1
    top_missing = max(skill_counts, key=skill_counts.get) if skill_counts else None

    return {
        "unopened_high_score": unopened,
        "overdue_followups": overdue,
        "avg_cv_this_week": avg_cv_this_week,
        "avg_cv_last_week": avg_cv_last_week,
        "top_missing_skill": top_missing,
    }


# ── Outreach queue helpers ──────────────────────────────────────────────────

def is_job_processed(job_id: str) -> bool:
    """Return True if this job_id already exists in outreach_queue."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM outreach_queue WHERE job_id = ?", (job_id,))
    result = cursor.fetchone()
    conn.close()
    return result is not None


def insert_outreach_draft(job_id: str, company: str, role: str,
                           hiring_manager: str, hm_email: str,
                           email_draft: str, linkedin_draft: str,
                           approval_token: str, llm_score: int,
                           llm_reason: str, apply_url: str = "",
                           hm_linkedin: str = "") -> None:
    """Insert a new outreach draft into the queue."""
    from datetime import datetime
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO outreach_queue
        (job_id, company, role, hiring_manager, hm_email, hm_linkedin,
         email_draft, linkedin_draft, approval_token,
         status, llm_score, llm_reason, created_at, apply_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        ON CONFLICT (job_id) DO NOTHING
    """, (job_id, company, role, hiring_manager, hm_email, hm_linkedin,
          email_draft, linkedin_draft, approval_token,
          llm_score, llm_reason, datetime.now().isoformat(), apply_url))
    conn.commit()
    conn.close()


def get_outreach_by_token(token: str) -> dict | None:
    """Return outreach_queue row for the given approval_token."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM outreach_queue WHERE approval_token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_outreach_status(token: str, status: str) -> None:
    """Update status for a given approval_token. Sets sent_at if status=sent."""
    from datetime import datetime
    conn = get_connection()
    cursor = conn.cursor()
    sent_at = datetime.now().isoformat() if status == "sent" else None
    cursor.execute(
        "UPDATE outreach_queue SET status = ?, sent_at = ? WHERE approval_token = ?",
        (status, sent_at, token)
    )
    conn.commit()
    conn.close()


def get_outreach_queue(status: str = None) -> list[dict]:
    """
    Return outreach_queue rows, optionally filtered by status.
    Status options: 'pending', 'sent', 'skipped', or None for all.
    """
    conn = get_connection()
    cursor = conn.cursor()
    if status:
        cursor.execute(
            "SELECT * FROM outreach_queue WHERE status = ? ORDER BY created_at DESC",
            (status,)
        )
    else:
        cursor.execute("SELECT * FROM outreach_queue ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_outreach_map() -> dict:
    """
    Return a dict mapping job_id → outreach info for all entries in the queue.
    Used by the jobs page to badge cards with draft/sent status without N+1 queries.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT job_id, status, approval_token, hm_email, hm_linkedin,
                  linkedin_draft, email_draft, hiring_manager
           FROM outreach_queue"""
    )
    rows = cursor.fetchall()
    conn.close()
    return {r["job_id"]: dict(r) for r in rows}


def update_outreach_draft(token: str, email_draft: str = None,
                          linkedin_draft: str = None) -> bool:
    """Update the draft text for an outreach item. Returns True if updated."""
    conn = get_connection()
    cursor = conn.cursor()
    if email_draft is not None and linkedin_draft is not None:
        cursor.execute(
            "UPDATE outreach_queue SET email_draft=?, linkedin_draft=? WHERE approval_token=?",
            (email_draft, linkedin_draft, token)
        )
    elif email_draft is not None:
        cursor.execute(
            "UPDATE outreach_queue SET email_draft=? WHERE approval_token=?",
            (email_draft, token)
        )
    elif linkedin_draft is not None:
        cursor.execute(
            "UPDATE outreach_queue SET linkedin_draft=? WHERE approval_token=?",
            (linkedin_draft, token)
        )
    updated = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return updated


# ===========================================================================
# Phase 1: Per-user helpers
# ===========================================================================
# job_listings stays shared. Per-user state (applied_status, cv_score,
# user_notes, follow_up_date, rejection_reason, hidden, preferences, CV,
# reminders) lives in user-scoped tables and is keyed by user_id.

import json as _json


def get_or_create_user(email: str, name: str = None, picture: str = None) -> int:
    """Look up a user by email; insert if missing. Returns the user's id."""
    if not email:
        raise ValueError("email is required")
    email = email.strip().lower()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    now = datetime.now().isoformat()
    if row:
        uid = row["id"]
        cursor.execute(
            "UPDATE users SET last_login = ?, name = COALESCE(?, name), picture = COALESCE(?, picture) WHERE id = ?",
            (now, name, picture, uid),
        )
    else:
        if USE_POSTGRES:
            cursor.execute(
                "INSERT INTO users (email, name, picture, created_at, last_login) "
                "VALUES (?, ?, ?, ?, ?) RETURNING id",
                (email, name, picture, now, now),
            )
            uid = cursor.fetchone()["id"]
        else:
            cursor.execute(
                "INSERT INTO users (email, name, picture, created_at, last_login) "
                "VALUES (?, ?, ?, ?, ?)",
                (email, name, picture, now, now),
            )
            uid = cursor.lastrowid
    conn.commit()
    conn.close()
    return uid


def get_user_by_email(email: str):
    if not email:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int):
    if not user_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# user_preferences
# ---------------------------------------------------------------------------

def get_user_preferences(user_id: int):
    """Return prefs dict for the user, or None if not set."""
    if not user_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT prefs_json FROM user_preferences WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    try:
        return _json.loads(row["prefs_json"])
    except (ValueError, TypeError):
        return None


def save_user_preferences(user_id: int, prefs: dict) -> None:
    if not user_id:
        raise ValueError("user_id is required")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO user_preferences (user_id, prefs_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            prefs_json = excluded.prefs_json,
            updated_at = excluded.updated_at
        """,
        (user_id, _json.dumps(prefs), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# user_cv_data
# ---------------------------------------------------------------------------

def get_user_cv_data(user_id: int):
    if not user_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT cv_json FROM user_cv_data WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    try:
        return _json.loads(row["cv_json"])
    except (ValueError, TypeError):
        return None


def save_user_cv_data(user_id: int, cv_data: dict) -> None:
    if not user_id:
        raise ValueError("user_id is required")
    conn = get_connection()
    cursor = conn.cursor()
    filename = (cv_data or {}).get("filename")
    cursor.execute(
        """
        INSERT INTO user_cv_data (user_id, cv_json, filename, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            cv_json = excluded.cv_json,
            filename = excluded.filename,
            updated_at = excluded.updated_at
        """,
        (user_id, _json.dumps(cv_data), filename, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def delete_user_cv_data(user_id: int) -> None:
    if not user_id:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_cv_data WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# user_job_state
# ---------------------------------------------------------------------------

_UJS_FIELDS = (
    "applied_status", "applied_date", "cv_score", "user_notes",
    "follow_up_date", "rejection_reason", "hidden",
)


def upsert_user_job_state(user_id: int, job_id: str, **fields) -> None:
    """
    Upsert per-user job state. Only the keys passed in **fields are updated;
    omitted fields are left untouched on existing rows.
    """
    if not user_id or not job_id:
        raise ValueError("user_id and job_id are required")
    bad = [k for k in fields if k not in _UJS_FIELDS]
    if bad:
        raise ValueError(f"unsupported user_job_state fields: {bad}")
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    # Make sure a row exists, then UPDATE only the supplied columns.
    cursor.execute(
        "INSERT INTO user_job_state (user_id, job_id, updated_at) VALUES (?, ?, ?) ON CONFLICT (user_id, job_id) DO NOTHING",
        (user_id, job_id, now),
    )
    if fields:
        set_sql = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ?"
        params = list(fields.values()) + [now, user_id, job_id]
        cursor.execute(
            f"UPDATE user_job_state SET {set_sql} WHERE user_id = ? AND job_id = ?",
            params,
        )
    conn.commit()
    conn.close()


def get_user_job_state(user_id: int, job_id: str):
    if not user_id or not job_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM user_job_state WHERE user_id = ? AND job_id = ?",
        (user_id, job_id),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_applied_status_user(user_id: int, job_id: str, status: int,
                               notes: str = None, follow_up_date: str = None,
                               rejection_reason: str = None) -> None:
    """User-scoped equivalent of update_applied_status."""
    fields = {"applied_status": int(status)}
    if notes is not None:
        fields["user_notes"] = notes
    if status == 1:
        fields["applied_date"] = datetime.now().isoformat()
    if follow_up_date is not None:
        fields["follow_up_date"] = follow_up_date
    if status == 6 and rejection_reason is not None:
        fields["rejection_reason"] = rejection_reason
    upsert_user_job_state(user_id, job_id, **fields)


def update_job_notes_user(user_id: int, job_id: str, notes: str) -> None:
    upsert_user_job_state(user_id, job_id, user_notes=notes)


def hide_job_user(user_id: int, job_id: str, hidden: bool = True) -> None:
    upsert_user_job_state(user_id, job_id, hidden=1 if hidden else 0)


def set_user_cv_score(user_id: int, job_id: str, cv_score_val: int) -> None:
    upsert_user_job_state(user_id, job_id, cv_score=int(cv_score_val or 0))


def get_unscored_jobs_for_user(user_id: int, limit: int = 200) -> list[dict]:
    """
    Return the N freshest jobs that have no user_job_state row yet for
    this user — i.e. jobs the user has never seen scored. Used by the
    lazy per-user scorer that runs on each /api/jobs hit.
    """
    if not user_id:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT j.job_id, j.role, j.company, j.location, j.job_description,
               j.remote_status, j.date_found
        FROM job_listings j
        LEFT JOIN user_job_state s
          ON s.job_id = j.job_id AND s.user_id = ?
        WHERE s.job_id IS NULL
        ORDER BY j.date_found DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def bulk_set_user_cv_scores(user_id: int, scores: dict) -> int:
    """
    scores: { job_id: cv_score_int }. Returns number of rows written.
    """
    if not user_id or not scores:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    written = 0
    for job_id, score in scores.items():
        cursor.execute(
            "INSERT INTO user_job_state (user_id, job_id, updated_at) VALUES (?, ?, ?) ON CONFLICT (user_id, job_id) DO NOTHING",
            (user_id, job_id, now),
        )
        cursor.execute(
            "UPDATE user_job_state SET cv_score = ?, updated_at = ? WHERE user_id = ? AND job_id = ?",
            (int(score or 0), now, user_id, job_id),
        )
        written += 1
    conn.commit()
    conn.close()
    return written


# ---------------------------------------------------------------------------
# User-scoped stats (mirror the global stat helpers above but filter by user)
# ---------------------------------------------------------------------------

def get_applied_count_user(user_id: int) -> int:
    if not user_id:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM user_job_state WHERE user_id = ? AND applied_status = 1",
        (user_id,),
    )
    n = cursor.fetchone()["cnt"]
    conn.close()
    return n


def get_saved_count_user(user_id: int) -> int:
    if not user_id:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM user_job_state WHERE user_id = ? AND applied_status = 2",
        (user_id,),
    )
    n = cursor.fetchone()["cnt"]
    conn.close()
    return n


def get_application_pipeline_stats_user(user_id: int) -> dict:
    """Per-user pipeline stage counts. Stages with zero rows still appear."""
    labels = {
        0: "New", 1: "Applied", 2: "Saved", 3: "Phone Screen",
        4: "Interview", 5: "Offer", 6: "Rejected",
    }
    result = {label: 0 for label in labels.values()}
    if not user_id:
        return result
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT applied_status, COUNT(*) AS cnt FROM user_job_state WHERE user_id = ? GROUP BY applied_status",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    for r in rows:
        label = labels.get(r["applied_status"], "New")
        result[label] = r["cnt"]
    return result


def get_comprehensive_stats_user(user_id: int) -> dict:
    """Per-user variant of get_comprehensive_stats."""
    return {
        "total_jobs": get_total_jobs(),
        "jobs_today": get_jobs_found_today(),
        "jobs_yesterday": get_jobs_found_yesterday(),
        "jobs_this_week": get_jobs_found_this_week(),
        "portal_stats": get_portal_stats(),
        "top_companies": get_top_companies(5),
        "top_roles": get_top_roles(5),
        "applied_count": get_applied_count_user(user_id),
        "saved_count": get_saved_count_user(user_id),
    }


def get_dashboard_insights_user(user_id: int) -> dict:
    """Per-user version of get_dashboard_insights."""
    from datetime import datetime, timedelta
    result = {
        "unopened_high_score": 0,
        "overdue_followups": 0,
        "avg_cv_this_week": 0,
        "avg_cv_last_week": 0,
        "top_missing_skill": None,
    }
    if not user_id:
        return result
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM job_listings j
        LEFT JOIN user_job_state s ON s.job_id = j.job_id AND s.user_id = ?
        WHERE j.relevance_score >= 75
          AND COALESCE(s.applied_status, 0) = 0
          AND COALESCE(s.hidden, 0) = 0
        """,
        (user_id,),
    )
    result["unopened_high_score"] = cursor.fetchone()["cnt"]

    today = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM user_job_state
        WHERE user_id = ?
          AND follow_up_date IS NOT NULL
          AND follow_up_date <= ?
          AND applied_status NOT IN (5, 6)
        """,
        (user_id, today),
    )
    result["overdue_followups"] = cursor.fetchone()["cnt"]

    week_start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    prev_week_start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    cursor.execute(
        """
        SELECT ROUND(AVG(s.cv_score), 1) AS avg
        FROM user_job_state s
        JOIN job_listings j ON j.job_id = s.job_id
        WHERE s.user_id = ? AND j.date_found >= ? AND s.cv_score > 0
        """,
        (user_id, week_start),
    )
    r = cursor.fetchone()
    result["avg_cv_this_week"] = float(r["avg"]) if r and r["avg"] is not None else 0

    cursor.execute(
        """
        SELECT ROUND(AVG(s.cv_score), 1) AS avg
        FROM user_job_state s
        JOIN job_listings j ON j.job_id = s.job_id
        WHERE s.user_id = ? AND j.date_found >= ? AND j.date_found < ? AND s.cv_score > 0
        """,
        (user_id, prev_week_start, week_start),
    )
    r = cursor.fetchone()
    result["avg_cv_last_week"] = float(r["avg"]) if r and r["avg"] is not None else 0

    cursor.execute(
        """
        SELECT j.job_description
        FROM job_listings j
        LEFT JOIN user_job_state s ON s.job_id = j.job_id AND s.user_id = ?
        WHERE COALESCE(s.applied_status, 0) = 0
          AND COALESCE(s.hidden, 0) = 0
        ORDER BY j.relevance_score DESC
        LIMIT 100
        """,
        (user_id,),
    )
    jd_rows = cursor.fetchall()
    conn.close()

    from analyzer import extract_skills
    skill_counts: dict = {}
    for jd_row in jd_rows:
        for skill in extract_skills(jd_row["job_description"] or "", max_skills=None):
            skill_counts[skill] = skill_counts.get(skill, 0) + 1
    if skill_counts:
        result["top_missing_skill"] = max(skill_counts, key=skill_counts.get)
    return result


def get_application_pipeline_stats_user_with_legacy_fallback(user_id):
    """
    During the rollout window, fall back to legacy job_listings columns when
    user_job_state has no rows for this user. This keeps the dashboard sensible
    if the migration script hasn't run yet.
    """
    stats = get_application_pipeline_stats_user(user_id)
    total = sum(stats.values())
    if total == 0:
        return get_application_pipeline_stats()
    return stats


# ---------------------------------------------------------------------------
# user_reminders
# ---------------------------------------------------------------------------

def get_user_reminders(user_id: int) -> list:
    if not user_id:
        return []
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT reminder_json FROM user_reminders WHERE user_id = ? ORDER BY id",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            out.append(_json.loads(r["reminder_json"]))
        except (ValueError, TypeError):
            continue
    return out


def save_user_reminders(user_id: int, reminders: list) -> None:
    """Replace the entire reminders list for the user."""
    if not user_id:
        raise ValueError("user_id is required")
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("DELETE FROM user_reminders WHERE user_id = ?", (user_id,))
    for r in (reminders or []):
        rid = str(r.get("id") or uuid_safe())
        cursor.execute(
            """
            INSERT INTO user_reminders (user_id, reminder_id, reminder_json, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, rid, _json.dumps(r), now),
        )
    conn.commit()
    conn.close()


def uuid_safe() -> str:
    import uuid as _uuid
    return _uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Per-user filter clause used by _build_jobs_query and similar consumers.
# Returns (join_sql, where_clauses, params) where the caller injects join_sql
# right after `FROM job_listings j` and AND-merges where_clauses.
# ---------------------------------------------------------------------------

def user_state_join_sql(user_id: int):
    """
    Return a LEFT JOIN against user_job_state for the given user.
    Always returns a tuple (join_sql, params) so callers can inject the
    parameter at the right position in their SQL parameter list.
    """
    join_sql = (
        "LEFT JOIN user_job_state ujs "
        "ON ujs.job_id = job_listings.job_id AND ujs.user_id = ?"
    )
    return join_sql, [int(user_id or 0)]


# ---------------------------------------------------------------------------
# Hiring managers — aggregated from outreach_queue + job_listings.poster_*
# ---------------------------------------------------------------------------

def get_hiring_managers(limit: int = 300) -> list[dict]:
    """
    Return distinct hiring managers / job posters with contact details and
    the latest role/company we associated them with.

    Sources (merged, deduped by lower(name)):
      * outreach_queue.hiring_manager / hm_email / hm_linkedin
      * job_listings.poster_name / poster_email / poster_linkedin

    For each person we keep the most-recent non-null linkedin / email and
    the last role+company we saw them associated with (most recent
    date_found / created_at). Sorted by `last_seen` descending.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT oq.hiring_manager AS name,
               oq.hm_email       AS email,
               oq.hm_linkedin    AS linkedin,
               oq.company        AS company,
               oq.role           AS role,
               oq.created_at     AS seen_at
        FROM outreach_queue oq
        WHERE oq.hiring_manager IS NOT NULL AND TRIM(oq.hiring_manager) <> ''
        """
    )
    rows = list(cursor.fetchall())

    cursor.execute(
        """
        SELECT j.poster_name     AS name,
               j.poster_email    AS email,
               j.poster_linkedin AS linkedin,
               j.company         AS company,
               j.role            AS role,
               j.date_found      AS seen_at
        FROM job_listings j
        WHERE j.poster_name IS NOT NULL AND TRIM(j.poster_name) <> ''
        """
    )
    rows.extend(cursor.fetchall())
    conn.close()

    by_name: dict = {}
    for r in rows:
        # psycopg dict_row → dict, sqlite Row → mapping; both support r["..."]
        name = (r["name"] or "").strip()
        if not name:
            continue
        key = name.lower()
        seen_at = r["seen_at"] or ""
        existing = by_name.get(key)
        candidate = {
            "name": name,
            "email": (r["email"] or "").strip() or None,
            "linkedin": (r["linkedin"] or "").strip() or None,
            "company": (r["company"] or "").strip() or None,
            "role": (r["role"] or "").strip() or None,
            "last_seen": seen_at,
        }
        if not existing:
            by_name[key] = candidate
            continue
        # Merge: prefer most recent seen_at for company/role/last_seen,
        # but keep the earliest-found non-null email/linkedin we already have.
        if seen_at and seen_at > (existing["last_seen"] or ""):
            existing["last_seen"] = seen_at
            existing["company"] = candidate["company"] or existing["company"]
            existing["role"] = candidate["role"] or existing["role"]
        existing["email"] = existing["email"] or candidate["email"]
        existing["linkedin"] = existing["linkedin"] or candidate["linkedin"]

    # Keep only entries we can actually act on:
    #   1. must have at least one of linkedin / email
    #   2. the "name" must look like a person, not a job title — the
    #      contact-scraper sometimes captures the role text into
    #      poster_name when no real human was found. We drop anything
    #      that smells like a role.
    actionable = [
        d for d in by_name.values()
        if (d["linkedin"] or d["email"]) and _looks_like_person_name(d["name"])
    ]
    actionable.sort(key=lambda d: d["last_seen"] or "", reverse=True)
    return actionable[: int(limit)]


# Words that almost certainly mean the field is a job title, not a person.
_ROLE_WORDS = frozenset({
    "manager", "engineer", "analyst", "developer", "designer", "architect",
    "consultant", "specialist", "lead", "director", "executive", "officer",
    "head", "vp", "founder", "owner", "principal", "associate", "intern",
    "scientist", "researcher", "writer", "editor", "marketer", "salesperson",
    "salesman", "saleswoman", "recruiter", "coordinator", "supervisor",
    "administrator", "technician", "operator", "assistant", "advisor",
    "strategist", "evangelist", "advocate", "trainee",
    # multi-word role markers
    "product", "data", "software", "marketing", "growth", "operations",
    "finance", "hr", "talent", "people", "customer", "client", "service",
    "support", "frontend", "backend", "full-stack", "fullstack", "devops",
    "platform", "infrastructure", "security", "qa", "test", "automation",
    "business", "digital", "content", "social",
    # generic noise
    "hiring", "team", "office", "remote", "fresher",
})

def _looks_like_person_name(name: str) -> bool:
    """
    Heuristic: does this string read like a person's name?
    Returns False for things like "Business Analyst", "Junior Developer",
    "Manager - Product", "Social Listening Analyst" — i.e. anything where
    a role-word appears at the start, plus a few structural giveaways.
    """
    if not name or not name.strip():
        return False
    n = name.strip()
    if len(n) > 60:
        return False
    # Job titles often use connector punctuation; person names don't.
    if any(ch in n for ch in (" - ", " | ", "/", "—", ":")):
        return False
    tokens = [t.lower().strip(",.()") for t in n.split() if t.strip()]
    if not tokens:
        return False
    # Lone token: only accept if it's clearly capitalised and not a role word.
    if len(tokens) == 1:
        return tokens[0] not in _ROLE_WORDS and n[0:1].isalpha() and n[0:1].isupper()
    # Multi-token: reject if ANY token is a role-word. People's names rarely
    # contain words like "Manager" or "Analyst".
    if any(t in _ROLE_WORDS for t in tokens):
        return False
    # Reject "all-caps shouting" — usually company names.
    if n.isupper() and len(n) > 4:
        return False
    return True
