"""
database.py - SQLite database operations for job listings tracking.
Handles creating tables, inserting/querying jobs, deduplication, and statistics.
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_IS_VERCEL = bool(os.environ.get("VERCEL"))
if _IS_VERCEL:
    DB_PATH = "/tmp/jobs.db"
else:
    DB_PATH = os.path.join(os.environ.get("DATA_DIR", _BASE_DIR), "jobs.db")


def get_connection():
    """Get a SQLite connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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

    # Add date_posted column for actual job posting date (idempotent)
    for col in ["date_posted TEXT"]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    # Add contact enrichment columns (idempotent)
    for col in ["poster_name TEXT", "poster_email TEXT", "poster_phone TEXT", "poster_linkedin TEXT"]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Phase 1b/1c: experience and salary range columns
    for col in [
        "experience_min INTEGER",
        "experience_max INTEGER",
        "salary_min INTEGER",
        "salary_max INTEGER",
    ]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    # Phase 1d: enhanced tracking columns
    for col in [
        "follow_up_date TEXT",
        "rejection_reason TEXT",
    ]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    # Phase 3a: company research columns
    for col in [
        "company_size TEXT",
        "company_funding_stage TEXT",
        "company_glassdoor_rating TEXT",
    ]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    # CV scoring column
    for col in ["cv_score INTEGER DEFAULT 0"]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

    # User actions: hide job
    for col in ["hidden INTEGER DEFAULT 0"]:
        try:
            cursor.execute(f"ALTER TABLE job_listings ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass

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
    except sqlite3.IntegrityError:
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
    """Get daily application counts for the last N days."""
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT DATE(date_found) as day, COUNT(*) as found,
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
    "Bengaluru": ["bengaluru", "bangalore", "whitefield", "koramangala", "indiranagar",
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


def get_jobs_for_reminder(keyword: str, min_score: int, max_jobs: int, since: str = None) -> list:
    """
    Return up to max_jobs listings whose role contains any of the comma-separated
    terms in `keyword` (case-insensitive, OR logic) and whose relevance_score >=
    min_score, ordered newest first. Only returns non-hidden jobs.

    since: ISO datetime string (e.g. last_sent). If provided, only returns jobs
           found after that timestamp, preventing duplicate sends.
    """
    terms = [t.strip().lower() for t in keyword.split(",") if t.strip()]
    if not terms:
        return []
    role_clauses = " OR ".join("LOWER(role) LIKE ?" for _ in terms)
    params = [f"%{t}%" for t in terms] + [int(min_score)]
    since_clause = ""
    if since:
        since_clause = "AND date_found > ?"
        params.append(since)
    params.append(int(max_jobs))
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT * FROM job_listings
        WHERE ({role_clauses})
          AND relevance_score >= ?
          {since_clause}
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
