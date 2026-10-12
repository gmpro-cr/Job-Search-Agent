"""Read-only funnel report for saved routines: why does a routine email 0 jobs?

For every routine it prints the filters and how many jobs survive each filter
stage that routine_runner / get_jobs_for_reminder applies, plus the top stored
scores. Prints no recipient addresses or CV text (the repo's Actions logs are
public). Writes nothing.

    python -m scripts.diagnose_routines
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_all_user_reminders, get_connection, get_user_preferences  # noqa: E402
from routine_runner import DEDUP_DAYS  # noqa: E402

RECENCY_DAYS = 3  # get_jobs_for_reminder's default max_age_days


def _mask(email):
    email = (email or "").strip()
    if "@" not in email:
        return "(none)"
    name, domain = email.split("@", 1)
    return f"{name[:2]}***@{domain}"


def _count(cursor, sql, params):
    cursor.execute(sql, params)
    return cursor.fetchone()["n"]


def report(user_id, r):
    keyword = (r.get("keyword") or "").strip()
    terms = [t.strip().lower() for t in keyword.split(",") if t.strip()]
    locs = [t.strip().lower() for t in (r.get("location") or "").split(",") if t.strip()]
    min_score = int(r.get("min_score", 50) or 50)
    posted_days = int(r["max_age_days"]) if r.get("max_age_days") else None
    print(f"  routine name={r.get('name')!r} enabled={r.get('enabled', True)} "
          f"recipient={_mask(r.get('email'))}")
    print(f"    keyword={keyword!r} location={r.get('location')!r} "
          f"min_score={min_score} max_age_days(posted)={posted_days} "
          f"max_jobs={r.get('max_jobs')}")
    if not terms:
        print("    -> SKIPPED by runner: empty keyword")
        return
    if not (r.get("email") or "").strip():
        print("    -> SKIPPED by runner: empty recipient")
        return

    conn = get_connection()
    cur = conn.cursor()
    where = ["(" + " OR ".join("LOWER(j.role) LIKE ?" for _ in terms) + ")"]
    params = [f"%{t}%" for t in terms]
    base = "SELECT COUNT(*) AS n FROM job_listings j "
    join = ("JOIN user_job_state s ON s.job_id = j.job_id AND s.user_id = ? ")

    def stage(label, extra_where=(), extra_params=(), with_join=False):
        sql = base + (join if with_join else "") + "WHERE " + " AND ".join(where + list(extra_where))
        p = ([user_id] if with_join else []) + params + list(extra_params)
        print(f"    {label:<44} {_count(cur, sql, p)}")

    recency = (datetime.now() - timedelta(days=RECENCY_DAYS)).isoformat()
    stage("1 role matches keyword (all time)")
    where.append("j.date_found > ?")
    params.append(recency)
    stage(f"2 + found in last {RECENCY_DAYS} days")
    if locs:
        where.append("(" + " OR ".join("LOWER(j.location) LIKE ?" for _ in locs) + ")")
        params += [f"%{l}%" for l in locs]
        stage("3 + location matches")
    if posted_days:
        cutoff = (datetime.now() - timedelta(days=posted_days)).date().isoformat()
        where.append("j.date_posted IS NOT NULL AND j.date_posted != '' AND j.date_posted >= ?")
        params.append(cutoff)
        stage(f"4 + date_posted within {posted_days} days")
    stage("5 + has a stored score for this user", ["s.cv_score IS NOT NULL"], with_join=True)
    stage(f"6 + score >= {min_score}", ["s.cv_score >= ?"], [min_score], with_join=True)
    stage("7 + not hidden", ["s.cv_score >= ?", "COALESCE(s.hidden, 0) = 0"],
          [min_score], with_join=True)
    sent_cutoff = (datetime.now() - timedelta(days=DEDUP_DAYS)).isoformat()
    stage(f"8 + not emailed in last {DEDUP_DAYS} days",
          ["s.cv_score >= ?", "COALESCE(s.hidden, 0) = 0",
           "(s.digest_sent_at IS NULL OR s.digest_sent_at <= ?)"],
          [min_score, sent_cutoff], with_join=True)

    # Top scores among jobs that passed the non-score filters.
    cur.execute(
        "SELECT j.role, j.location, j.date_posted, s.cv_score FROM job_listings j "
        + join + "WHERE " + " AND ".join(where) +
        " AND s.cv_score IS NOT NULL ORDER BY s.cv_score DESC LIMIT 8",
        [user_id] + params)
    rows = cur.fetchall()
    print("    top scores after non-score filters:")
    for row in rows:
        print(f"      {row['cv_score']:>3}  {row['role'][:60]!r}  "
              f"loc={str(row['location'])[:30]!r} posted={row['date_posted']!r}")
    if not rows:
        print("      (none)")
    conn.close()


def main():
    items = get_all_user_reminders()
    print(f"{len(items)} user(s) with routines")
    for user_id, reminders in items:
        prefs = get_user_preferences(user_id) or {}
        print(f"\nuser {user_id}: {len(reminders)} routine(s); "
              f"scored_with={prefs.get('_scored_with')!r} "
              f"scoring_profile={prefs.get('scoring_profile')!r} "
              f"job_titles={prefs.get('job_titles')!r}")
        for r in reminders:
            report(user_id, r)


if __name__ == "__main__":
    main()
