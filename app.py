"""
app.py - Flask web UI for Job Search Agent.
Provides a browser-based interface for managing preferences, running the scraper,
viewing jobs, and browsing digests.
"""

import os
import sys
import logging
import threading
import uuid
from datetime import datetime, timedelta

# Load .env before anything else reads os.environ
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_from_directory,
)

# Ensure project root is on the path so we can import sibling modules
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from main import load_config, load_preferences, save_preferences, DEFAULT_PREFS, apply_env_overrides, _CREDENTIAL_KEYS
from database import (
    init_db, get_connection, get_comprehensive_stats, get_portal_quality_stats,
    update_applied_status, insert_jobs_bulk, generate_job_id, mark_sent_in_digest,
    get_unsent_jobs, update_job_contacts, get_distinct_locations,
    get_normalized_locations, normalize_location, _CITY_PATTERNS,
    get_application_pipeline_stats, get_best_matching_categories,
    get_application_activity, get_recommended_actions,
    hide_job, update_job_notes, dedup_jobs,
    _INTERNATIONAL_CANONICALS, _INTERNATIONAL_KEYWORDS,
)
from scrapers import scrape_all_portals
from analyzer import analyze_jobs, generate_tailored_points, parse_nlp_query, parse_cv_text, cv_score, compute_gap_analysis, load_cv_data, save_cv_data, CV_DATA_PATH
from digest_generator import generate_digest, get_latest_digest, DIGEST_DIR
from email_notifier import send_job_email
from reminder_runner import run_reminders
from database import delete_old_jobs
from contact_scraper import enrich_jobs_with_contacts
from telegram_notifier import send_telegram_alert, send_telegram_batch_summary
from telegram_bot import start_telegram_bot

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

_IS_VERCEL = bool(os.environ.get("VERCEL"))

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "job-search-agent-dev-key")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize the database on startup
try:
    init_db()
except Exception as e:
    logger.warning("Database init warning (may be expected on Vercel): %s", e)

# ---------------------------------------------------------------------------
# Background scraper state
# ---------------------------------------------------------------------------

scraper_status = {
    "running": False,
    "phase": "idle",
    "portal_progress": {},
    "done_portals": 0,
    "total_portals": 0,
    "total_jobs": 0,
    "qualified_jobs": 0,
    "inserted": 0,
    "skipped": 0,
    "digest_path": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
scraper_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Live search state
# ---------------------------------------------------------------------------

live_search_status = {
    "running": False,
    "phase": "idle",
    "portal_progress": {},
    "done_portals": 0,
    "total_portals": 0,
    "total_jobs": 0,
    "qualified_jobs": 0,
    "inserted": 0,
    "skipped": 0,
    "error": None,
    "started_at": None,
    "finished_at": None,
    "result_job_ids": [],
}
live_search_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Daily scheduler (11:00 AM)
# ---------------------------------------------------------------------------

_scheduler = None


def _scheduled_pipeline_run():
    """Callback for the daily scheduled scraper run."""
    global scraper_status
    with scraper_lock:
        if scraper_status["running"]:
            logger.info("Scheduled run skipped - scraper is already running")
            return
        scraper_status = {
            "running": True,
            "phase": "starting",
            "portal_progress": {},
            "done_portals": 0,
            "total_portals": 0,
            "total_jobs": 0,
            "qualified_jobs": 0,
            "inserted": 0,
            "skipped": 0,
            "digest_path": None,
            "error": None,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
        }
    logger.info("Scheduled daily pipeline run starting")
    _run_scraper_pipeline()


def setup_background_scheduler():
    """Start APScheduler with two fixed daily pipeline runs: 07:00 and 19:00."""
    global _scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(
            _scheduled_pipeline_run,
            trigger=CronTrigger(hour=7, minute=0),
            id="morning_pipeline",
            name="Morning job scraper pipeline at 07:00",
            replace_existing=True,
        )
        _scheduler.add_job(
            _scheduled_pipeline_run,
            trigger=CronTrigger(hour=19, minute=0),
            id="evening_pipeline",
            name="Evening job scraper pipeline at 19:00",
            replace_existing=True,
        )
        _scheduler.start()
        logger.info("Background scheduler started - pipeline runs at 07:00 and 19:00 daily")
    except ImportError:
        logger.warning("APScheduler not installed - daily scheduling disabled")
    except Exception as e:
        logger.error("Failed to start background scheduler: %s", e)


def _run_apollo_enrichment(job_ids):
    """Run contact enrichment for a list of job IDs using the contact scraper."""
    conn = get_connection()
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in job_ids)
    cursor.execute(
        f"SELECT job_id, company, job_description, apply_url FROM job_listings "
        f"WHERE job_id IN ({placeholders}) "
        f"AND (poster_email IS NULL OR poster_email = '')",
        job_ids,
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not rows:
        return

    contacts = enrich_jobs_with_contacts(rows)
    for jid, info in contacts.items():
        update_job_contacts(
            jid,
            info.get("poster_name", ""),
            info.get("poster_email", ""),
            info.get("poster_phone", ""),
            info.get("poster_linkedin", ""),
        )


def _run_scraper_pipeline():
    """Run the full pipeline in a background thread."""
    global scraper_status
    try:
        config = load_config()
        preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
        job_titles = preferences.get("job_titles", DEFAULT_PREFS["job_titles"])
        locations = preferences.get("locations", DEFAULT_PREFS["locations"])
        top_n = preferences.get("top_jobs_per_digest", 5)

        # Phase 1: Scrape
        with scraper_lock:
            scraper_status["phase"] = "scraping"
            scraper_status["portal_progress"] = {}

        def scrape_cb(portal, status, count, done, total):
            with scraper_lock:
                scraper_status["portal_progress"][portal] = {
                    "status": status, "count": count,
                }
                scraper_status["done_portals"] = done
                scraper_status["total_portals"] = total

        all_jobs, portal_results = scrape_all_portals(
            job_titles, locations, config, progress_callback=scrape_cb,
        )

        with scraper_lock:
            scraper_status["total_jobs"] = len(all_jobs)

        if not all_jobs:
            with scraper_lock:
                scraper_status["phase"] = "done"
                scraper_status["finished_at"] = datetime.now().isoformat()
                scraper_status["running"] = False
            return

        # Phase 2: Analyze
        with scraper_lock:
            scraper_status["phase"] = "analyzing"

        qualified_jobs, all_analyzed = analyze_jobs(all_jobs, preferences, config)

        with scraper_lock:
            scraper_status["qualified_jobs"] = len(qualified_jobs)

        # Phase 3: Store
        with scraper_lock:
            scraper_status["phase"] = "storing"

        for job in all_analyzed:
            job["job_id"] = generate_job_id(
                job["portal"], job["company"], job["role"], job.get("location", ""),
            )
        inserted, skipped = insert_jobs_bulk(all_analyzed)

        with scraper_lock:
            scraper_status["inserted"] = inserted
            scraper_status["skipped"] = skipped

        # Phase 3.5: Telegram alerts
        tg_token = preferences.get("telegram_bot_token", "").strip()
        tg_chat = preferences.get("telegram_chat_id", "").strip()
        tg_min = int(preferences.get("telegram_min_score", 65))
        if tg_token and tg_chat:
            with scraper_lock:
                scraper_status["phase"] = "telegram_alerts"
            alert_count = 0
            for job in qualified_jobs:
                if job.get("relevance_score", 0) >= tg_min:
                    send_telegram_alert(job, tg_token, tg_chat)
                    alert_count += 1
            if alert_count > 0 or inserted > 0:
                send_telegram_batch_summary(len(all_jobs), len(qualified_jobs), inserted, tg_token, tg_chat)
            logger.info("Sent %d Telegram alerts", alert_count)

        # Phase 3.6: Contact enrichment (via scraper, no API key needed)
        with scraper_lock:
            scraper_status["phase"] = "enriching_contacts"
        all_job_ids = [j["job_id"] for j in all_analyzed if j.get("job_id")]
        if all_job_ids:
            _run_apollo_enrichment(all_job_ids)

        # Phase 4: Digest
        with scraper_lock:
            scraper_status["phase"] = "generating_digest"

        digest_jobs = qualified_jobs[:top_n]
        stats = get_comprehensive_stats()
        html_path, _ = generate_digest(
            digest_jobs, portal_results, preferences, stats, open_browser=False,
        )

        sent_ids = [j.get("job_id") for j in digest_jobs if j.get("job_id")]
        if sent_ids:
            mark_sent_in_digest(sent_ids)

        with scraper_lock:
            scraper_status["digest_path"] = os.path.basename(html_path)

        # Phase 5: Email notification
        recipient = preferences.get("email", "").strip()
        gmail_addr = preferences.get("gmail_address", "").strip()
        gmail_pass = preferences.get("gmail_app_password", "").strip()
        if recipient and gmail_addr and gmail_pass:
            with scraper_lock:
                scraper_status["phase"] = "sending_email"
            try:
                email_jobs = digest_jobs if digest_jobs else []
                send_job_email(recipient, email_jobs, preferences)
                logger.info("Email digest sent to %s", recipient)
            except Exception as e:
                logger.error("Failed to send email: %s", e)

        # Phase 5.5: Cleanup old jobs (>30 days)
        with scraper_lock:
            scraper_status["phase"] = "cleanup"
        try:
            removed = delete_old_jobs(days=30)
            logger.info("Cleanup: removed %d jobs older than 30 days", removed)
        except Exception as e:
            logger.error("Cleanup error: %s", e)

        # Phase 5.6: Custom reminders
        with scraper_lock:
            scraper_status["phase"] = "reminders"
        try:
            run_reminders(preferences)
        except Exception as e:
            logger.error("Reminders error: %s", e)

        with scraper_lock:
            scraper_status["phase"] = "done"
            scraper_status["finished_at"] = datetime.now().isoformat()
            scraper_status["running"] = False

    except Exception as e:
        logger.exception("Scraper pipeline error")
        with scraper_lock:
            scraper_status["error"] = str(e)
            scraper_status["phase"] = "error"
            scraper_status["running"] = False


def _run_live_search(query, location):
    """Run a slim scrape+analyze+store pipeline for live search from the jobs page."""
    global live_search_status
    try:
        config = load_config()
        preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())

        job_titles = [query] if query else preferences.get("job_titles", DEFAULT_PREFS["job_titles"])
        locations_list = [location] if location else preferences.get("locations", DEFAULT_PREFS["locations"])

        # Phase 1: Scrape
        with live_search_lock:
            live_search_status["phase"] = "scraping"
            live_search_status["portal_progress"] = {}

        def scrape_cb(portal, status, count, done, total):
            with live_search_lock:
                live_search_status["portal_progress"][portal] = {
                    "status": status, "count": count,
                }
                live_search_status["done_portals"] = done
                live_search_status["total_portals"] = total

        all_jobs, portal_results = scrape_all_portals(
            job_titles, locations_list, config, progress_callback=scrape_cb,
        )

        with live_search_lock:
            live_search_status["total_jobs"] = len(all_jobs)

        if not all_jobs:
            with live_search_lock:
                live_search_status["phase"] = "done"
                live_search_status["finished_at"] = datetime.now().isoformat()
                live_search_status["running"] = False
            return

        # Phase 2: Analyze
        with live_search_lock:
            live_search_status["phase"] = "analyzing"

        qualified_jobs, all_analyzed = analyze_jobs(all_jobs, preferences, config)

        with live_search_lock:
            live_search_status["qualified_jobs"] = len(qualified_jobs)

        # Phase 3: Store
        with live_search_lock:
            live_search_status["phase"] = "storing"

        for job in all_analyzed:
            job["job_id"] = generate_job_id(
                job["portal"], job["company"], job["role"], job.get("location", ""),
            )
        inserted, skipped = insert_jobs_bulk(all_analyzed)
        result_ids = [j["job_id"] for j in all_analyzed if j.get("job_id")]

        with live_search_lock:
            live_search_status["inserted"] = inserted
            live_search_status["skipped"] = skipped
            live_search_status["result_job_ids"] = result_ids

        # Phase 3.5: Telegram alerts
        tg_token = preferences.get("telegram_bot_token", "").strip()
        tg_chat = preferences.get("telegram_chat_id", "").strip()
        tg_min = int(preferences.get("telegram_min_score", 65))
        if tg_token and tg_chat:
            with live_search_lock:
                live_search_status["phase"] = "telegram_alerts"
            for job in qualified_jobs:
                if job.get("relevance_score", 0) >= tg_min:
                    send_telegram_alert(job, tg_token, tg_chat)

        # Phase 4: Contact enrichment (via scraper, no API key needed)
        if result_ids:
            with live_search_lock:
                live_search_status["phase"] = "enriching_contacts"
            _run_apollo_enrichment(result_ids)

        with live_search_lock:
            live_search_status["phase"] = "done"
            live_search_status["finished_at"] = datetime.now().isoformat()
            live_search_status["running"] = False

    except Exception as e:
        logger.exception("Live search pipeline error")
        with live_search_lock:
            live_search_status["error"] = str(e)
            live_search_status["phase"] = "error"
            live_search_status["running"] = False


# ---------------------------------------------------------------------------
# Scheduler & Telegram bot startup guards
# ---------------------------------------------------------------------------
# Skip on Vercel (serverless – no persistent processes).
# Flask dev mode: only start in the reloader child (WERKZEUG_RUN_MAIN=true).
# Gunicorn with --preload: code runs once in the arbiter, then workers fork.
#   The arbiter imports gunicorn.arbiter, workers do not – use that to detect
#   we are in a preloaded arbiter and start background tasks there (they
#   survive fork because they are daemon threads).
# Gunicorn without --preload: each worker imports app.py; with 1 worker this
#   is fine – start unconditionally when not in debug mode.


def _is_gunicorn_arbiter():
    """Return True if we are running inside the gunicorn arbiter (master)."""
    return "gunicorn" in os.environ.get("SERVER_SOFTWARE", "")


def _should_start_background_tasks():
    if _IS_VERCEL:
        return False
    # Flask dev server with reloader
    if app.debug:
        return os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    # Gunicorn or any other production server
    return True


if _should_start_background_tasks():
    setup_background_scheduler()

    # Start Telegram bot if token is configured
    _bot_prefs = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    _bot_token = _bot_prefs.get("telegram_bot_token", "").strip()
    if _bot_token:
        start_telegram_bot(_bot_token)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/favicon.ico")
def favicon():
    # Return 204 No Content to avoid 500 errors when favicon is missing
    return "", 204


@app.route("/dashboard")
def dashboard():
    stats = get_comprehensive_stats()
    portal_quality = get_portal_quality_stats()
    pipeline = get_application_pipeline_stats()
    categories = get_best_matching_categories()
    activity = get_application_activity()
    recommendations = get_recommended_actions()
    return render_template(
        "dashboard.html", stats=stats, portal_quality=portal_quality,
        pipeline=pipeline, categories=categories, activity=activity,
        recommendations=recommendations,
    )


def _build_jobs_query(filters):
    """
    Build SQL WHERE clause and params from a filters dict.
    Returns (conditions, params, order) where conditions is a list of SQL fragments.
    """
    conditions = []
    params = []

    search = filters.get("search", "")
    portal = filters.get("portal", "")
    remote = filters.get("remote", "")
    company_type = filters.get("company_type", "")
    sort = filters.get("sort", "date_desc")
    applied = filters.get("applied", "")
    location = filters.get("location", "")
    recency = filters.get("recency", "")
    min_score = filters.get("min_score", "0")
    experience = filters.get("experience", "")
    salary_min = filters.get("salary_min", "")
    salary_max = filters.get("salary_max", "")

    # Always exclude hidden jobs (hidden = 1) unless explicitly requested
    if not filters.get("show_hidden"):
        conditions.append("(hidden = 0 OR hidden IS NULL)")

    # Exclude international locations by default unless a specific location is
    # chosen (in which case the user knows what they're filtering to) or
    # show_international is explicitly set.
    if not filters.get("location") and not filters.get("show_international"):
        intl = list(_INTERNATIONAL_CANONICALS)
        intl_ph = ",".join("?" for _ in intl)
        kw_conds = " OR ".join("LOWER(location) LIKE ?" for _ in _INTERNATIONAL_KEYWORDS)
        conditions.append(
            f"(location IS NULL OR location = '' OR "
            f"(location NOT IN ({intl_ph}) AND NOT ({kw_conds})))"
        )
        params.extend(intl)
        params.extend(f"%{kw}%" for kw in _INTERNATIONAL_KEYWORDS)

    # Default minimum score filter (0 = show all)
    try:
        min_score_val = int(min_score)
    except (ValueError, TypeError):
        min_score_val = 60
    if min_score_val > 0:
        conditions.append("relevance_score >= ?")
        params.append(min_score_val)

    if search:
        conditions.append("(role LIKE ? OR company LIKE ? OR job_description LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if portal:
        conditions.append("portal = ?")
        params.append(portal)
    if remote:
        conditions.append("remote_status = ?")
        params.append(remote)
    if company_type:
        conditions.append("company_type = ?")
        params.append(company_type)
    if location:
        city_patterns = _CITY_PATTERNS.get(location)
        if city_patterns:
            like_clauses = ["location LIKE ?" for _ in city_patterns]
            conditions.append("(" + " OR ".join(like_clauses) + ")")
            params.extend([f"%{p}%" for p in city_patterns])
        else:
            conditions.append("location LIKE ?")
            params.append(f"%{location}%")
    if recency:
        recency_map = {
            "24h": timedelta(hours=24),
            "3d": timedelta(days=3),
            "1w": timedelta(weeks=1),
            "1m": timedelta(days=30),
        }
        td = recency_map.get(recency)
        if td:
            cutoff_date = (datetime.now() - td).strftime("%Y-%m-%d")
            conditions.append(
                "(date_posted IS NOT NULL AND date_posted != '' AND date_posted >= ?)"
            )
            params.append(cutoff_date)

    applied_map = {
        "none": "applied_status = 0",
        "applied": "applied_status = 1",
        "saved": "applied_status = 2",
        "phone_screen": "applied_status = 3",
        "interview": "applied_status = 4",
        "offer": "applied_status = 5",
        "rejected": "applied_status = 6",
    }
    if applied in applied_map:
        conditions.append(applied_map[applied])

    if experience:
        exp_ranges = {
            "0-3": (0, 3),
            "3-7": (3, 7),
            "7-12": (7, 12),
            "12+": (12, 99),
        }
        exp_range = exp_ranges.get(experience)
        if exp_range:
            lo, hi = exp_range
            conditions.append(
                "(experience_min IS NOT NULL AND experience_min <= ? AND experience_max >= ?)"
            )
            params.extend([hi, lo])

    if salary_min:
        try:
            sal_min_inr = int(salary_min) * 100_000
            conditions.append("(salary_min IS NOT NULL AND salary_max >= ?)")
            params.append(sal_min_inr)
        except (ValueError, TypeError):
            pass
    if salary_max:
        try:
            sal_max_inr = int(salary_max) * 100_000
            conditions.append("(salary_min IS NOT NULL AND salary_min <= ?)")
            params.append(sal_max_inr)
        except (ValueError, TypeError):
            pass

    sort_map = {
        "score_desc": "relevance_score DESC",
        "score_asc": "relevance_score ASC",
        "date_desc": "date_found DESC",
        "date_asc": "date_found ASC",
        "company_asc": "company ASC",
    }
    order = sort_map.get(sort, "date_found DESC")

    return conditions, params, order


@app.route("/jobs")
def jobs():
    # Read filter params
    filters = {
        "search": request.args.get("search", "").strip(),
        "portal": request.args.get("portal", ""),
        "remote": request.args.get("remote", ""),
        "company_type": request.args.get("company_type", ""),
        "sort": request.args.get("sort", "date_desc"),
        "applied": request.args.get("applied", ""),
        "location": request.args.get("location", ""),
        "recency": request.args.get("recency", ""),
        "min_score": request.args.get("min_score", "0"),
        "experience": request.args.get("experience", ""),
        "salary_min": request.args.get("salary_min", ""),
        "salary_max": request.args.get("salary_max", ""),
    }
    conditions, params, order = _build_jobs_query(filters)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    conn = get_connection()
    cursor = conn.cursor()

    # Fetch all matching jobs (no pagination)
    cursor.execute(
        f"SELECT * FROM job_listings{where} ORDER BY {order}",
        params,
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    total = len(rows)

    # Get distinct portals for filter dropdown
    conn2 = get_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT DISTINCT portal FROM job_listings ORDER BY portal")
    portals = [r["portal"] for r in cur2.fetchall()]
    conn2.close()

    # Get normalized locations for filter dropdown (canonical name + count)
    normalized_locs = get_normalized_locations()

    clean_filters = {k: v for k, v in filters.items() if v and v != "0"}

    return render_template(
        "jobs.html",
        jobs=rows, total=total,
        portals=portals, locations=normalized_locs,
        filters=filters, clean_filters=clean_filters,
    )


@app.route("/api/nlp-search", methods=["POST"])
def nlp_search():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "Empty query"}), 400

    config = load_config()
    filters = parse_nlp_query(query, config)

    # Default min_score to 0 for NLP search (show all matching jobs)
    if "min_score" not in filters:
        filters["min_score"] = "0"

    conditions, params, order = _build_jobs_query(filters)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(f"SELECT COUNT(*) as cnt FROM job_listings{where}", params)
    total = cursor.fetchone()["cnt"]

    cursor.execute(
        f"SELECT * FROM job_listings{where} ORDER BY {order} LIMIT 25",
        params,
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Build human-readable filter descriptions
    filter_labels = []
    if filters.get("search"):
        filter_labels.append(f'Search: "{filters["search"]}"')
    if filters.get("location"):
        filter_labels.append(f'Location: {filters["location"]}')
    if filters.get("remote"):
        filter_labels.append(f'Remote: {filters["remote"].title()}')
    if filters.get("salary_min"):
        filter_labels.append(f'Salary > {filters["salary_min"]}L')
    if filters.get("salary_max"):
        filter_labels.append(f'Salary < {filters["salary_max"]}L')
    if filters.get("experience"):
        filter_labels.append(f'Experience: {filters["experience"]} yrs')
    if filters.get("company_type"):
        filter_labels.append(f'Company: {filters["company_type"].title()}')
    if filters.get("applied"):
        filter_labels.append(f'Status: {filters["applied"]}')

    return jsonify({
        "ok": True,
        "query": query,
        "filters": filters,
        "filter_labels": filter_labels,
        "jobs": rows,
        "total": total,
    })


@app.route("/api/jobs/<job_id>/status", methods=["POST"])
def update_job_status(job_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status", 0)
    notes = data.get("notes")
    follow_up_date = data.get("follow_up_date")
    rejection_reason = data.get("rejection_reason")
    try:
        status = int(status)
    except (ValueError, TypeError):
        status = 0
    update_applied_status(job_id, status, notes, follow_up_date, rejection_reason)
    return jsonify({"ok": True, "job_id": job_id, "status": status})


@app.route("/api/jobs/<job_id>/hide", methods=["POST"])
def hide_job_route(job_id):
    """Hide or unhide a job. Body: {hidden: true/false}"""
    data = request.get_json(silent=True) or {}
    is_hidden = bool(data.get("hidden", True))
    hide_job(job_id, is_hidden)
    return jsonify({"ok": True, "job_id": job_id, "hidden": is_hidden})


@app.route("/api/jobs/<job_id>/notes", methods=["POST"])
def save_job_notes(job_id):
    """Save user notes for a job without changing other fields."""
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "")
    update_job_notes(job_id, notes)
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/admin/dedup", methods=["POST"])
def admin_dedup():
    """Remove cross-portal duplicate jobs, keeping highest-scoring copy."""
    deleted = dedup_jobs()
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/preferences", methods=["GET", "POST"])
def preferences():
    config = load_config()
    if request.method == "POST":
        # Normalize digest_time: "11.00 AM" → "11:00 AM"
        raw_dt = request.form.get("digest_time", "11:00 AM").strip()
        if "." in raw_dt:
            dt_parts = raw_dt.split(" ", 1)
            dt_parts[0] = dt_parts[0].replace(".", ":")
            raw_dt = " ".join(dt_parts)

        prefs = {
            "job_titles": [
                t.strip() for t in request.form.get("job_titles", "").split(",") if t.strip()
            ],
            "locations": [
                l.strip() for l in request.form.get("locations", "").split(",") if l.strip()
            ],
            "industries": [
                i.strip() for i in request.form.get("industries", "").split(",") if i.strip()
            ],
            "transferable_skills": [
                s.strip() for s in request.form.get("transferable_skills", "").split(",") if s.strip()
            ],
            "top_jobs_per_digest": max(3, min(10, int(request.form.get("top_jobs", "5")))),
            "digest_time": raw_dt,
            "email": request.form.get("email", "").strip(),
            "gmail_address": request.form.get("gmail_address", "").strip(),
            "gmail_app_password": request.form.get("gmail_app_password", "").strip(),
            "apollo_api_key": request.form.get("apollo_api_key", "").strip(),
            "telegram_bot_token": request.form.get("telegram_bot_token", "").strip(),
            "telegram_chat_id": request.form.get("telegram_chat_id", "").strip(),
            "telegram_min_score": max(0, min(100, int(request.form.get("telegram_min_score", "65")))),
        }
        save_preferences(prefs)
        flash("Preferences saved successfully!", "success")
        return redirect(url_for("preferences"))

    prefs = load_preferences() or DEFAULT_PREFS.copy()
    # Pre-populate transferable_skills from defaults if the user hasn't set them yet
    if not prefs.get("transferable_skills"):
        prefs["transferable_skills"] = DEFAULT_PREFS["transferable_skills"]
    # Tell the template which credential fields are set via env vars
    env_credentials = {
        "gmail_app_password": bool(os.environ.get("GMAIL_APP_PASSWORD")),
        "telegram_bot_token": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
        "apollo_api_key": bool(os.environ.get("APOLLO_API_KEY")),
    }
    return render_template("preferences.html", prefs=prefs, config=config, env_credentials=env_credentials)


@app.route("/api/jobs/<job_id>/tailored-points")
def tailored_points(job_id):
    """Generate tailored resume bullet points for a specific job."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM job_listings WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return jsonify({"ok": False, "error": "Job not found"}), 404
    job = dict(row)
    config = load_config()
    preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    points = generate_tailored_points(job, preferences, config)
    return jsonify({"ok": True, "points": points})


@app.route("/scraper")
def scraper():
    return render_template("scraper.html")


@app.route("/api/jobs/import", methods=["POST"])
def import_jobs():
    """Accept scraped jobs from external sources (e.g. GitHub Actions)."""
    data = request.get_json(silent=True) or {}

    # Validate secret
    import_secret = os.environ.get("IMPORT_SECRET", "")
    if not import_secret:
        return jsonify({"ok": False, "error": "IMPORT_SECRET not configured on server"}), 500
    if data.get("secret") != import_secret:
        return jsonify({"ok": False, "error": "Invalid secret"}), 403

    jobs = data.get("jobs")
    if not jobs or not isinstance(jobs, list):
        return jsonify({"ok": False, "error": "Missing or invalid 'jobs' array"}), 400

    # Generate job IDs and insert
    for job in jobs:
        job["job_id"] = generate_job_id(
            job.get("portal", "unknown"),
            job.get("company", ""),
            job.get("role", ""),
            job.get("location", ""),
        )
    inserted, skipped = insert_jobs_bulk(jobs)
    logger.info("Import API: inserted=%d, skipped=%d (total submitted=%d)", inserted, skipped, len(jobs))

    # Telegram alerts for qualified jobs
    preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    tg_token = preferences.get("telegram_bot_token", "").strip()
    tg_chat = preferences.get("telegram_chat_id", "").strip()
    tg_min = int(preferences.get("telegram_min_score", 65))
    alert_count = 0
    if tg_token and tg_chat:
        for job in jobs:
            if job.get("relevance_score", 0) >= tg_min:
                try:
                    send_telegram_alert(job, tg_token, tg_chat)
                    alert_count += 1
                except Exception as e:
                    logger.warning("Telegram alert failed for %s: %s", job.get("job_id"), e)
        if alert_count > 0 or inserted > 0:
            try:
                send_telegram_batch_summary(len(jobs), alert_count, inserted, tg_token, tg_chat)
            except Exception as e:
                logger.warning("Telegram batch summary failed: %s", e)

    return jsonify({"ok": True, "inserted": inserted, "skipped": skipped, "alerts": alert_count})


@app.route("/api/scraper/start", methods=["POST"])
def start_scraper():
    if _IS_VERCEL:
        return jsonify({"ok": False, "error": "Scraper is not available in cloud mode. Run the scraper locally with: python main.py"}), 503
    global scraper_status
    with scraper_lock:
        if scraper_status["running"]:
            return jsonify({"ok": False, "error": "Scraper is already running"}), 409
        scraper_status = {
            "running": True,
            "phase": "starting",
            "portal_progress": {},
            "done_portals": 0,
            "total_portals": 0,
            "total_jobs": 0,
            "qualified_jobs": 0,
            "inserted": 0,
            "skipped": 0,
            "digest_path": None,
            "error": None,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
        }
    t = threading.Thread(target=_run_scraper_pipeline, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/scraper/status")
def scraper_status_api():
    with scraper_lock:
        return jsonify(dict(scraper_status))


# ---------------------------------------------------------------------------
# Live Search API
# ---------------------------------------------------------------------------

@app.route("/api/search/start", methods=["POST"])
def start_live_search():
    if _IS_VERCEL:
        return jsonify({"ok": False, "error": "Live search is not available in cloud mode. Run the scraper locally with: python main.py"}), 503
    global live_search_status
    data = request.get_json(silent=True) or {}
    query = data.get("query", "").strip()
    location = data.get("location", "").strip()

    with live_search_lock:
        if live_search_status["running"]:
            return jsonify({"ok": False, "error": "A search is already running"}), 409
        live_search_status = {
            "running": True,
            "phase": "starting",
            "portal_progress": {},
            "done_portals": 0,
            "total_portals": 0,
            "total_jobs": 0,
            "qualified_jobs": 0,
            "inserted": 0,
            "skipped": 0,
            "error": None,
            "started_at": datetime.now().isoformat(),
            "finished_at": None,
            "result_job_ids": [],
        }
    t = threading.Thread(target=_run_live_search, args=(query, location), daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/search/status")
def live_search_status_api():
    with live_search_lock:
        return jsonify(dict(live_search_status))


# ---------------------------------------------------------------------------
# Scheduler & Digests
# ---------------------------------------------------------------------------

@app.route("/api/scheduler/status")
def scheduler_status():
    """Return the current scheduler state and next run time."""
    if _scheduler and _scheduler.running:
        morning_job = _scheduler.get_job("morning_pipeline")
        evening_job = _scheduler.get_job("evening_pipeline")
        job = morning_job or evening_job
        if job:
            next_run = job.next_run_time
            return jsonify({
                "enabled": True,
                "next_run": next_run.isoformat() if next_run else None,
                "next_run_human": next_run.strftime("%B %d, %Y at %I:%M %p") if next_run else None,
            })
    return jsonify({"enabled": False, "next_run": None, "next_run_human": None})


@app.route("/digests")
def digests():
    files = []
    if os.path.isdir(DIGEST_DIR):
        for f in sorted(os.listdir(DIGEST_DIR), reverse=True):
            if f.endswith(".html"):
                path = os.path.join(DIGEST_DIR, f)
                mtime = os.path.getmtime(path)
                files.append({
                    "filename": f,
                    "date": datetime.fromtimestamp(mtime).strftime("%B %d, %Y %I:%M %p"),
                    "size_kb": round(os.path.getsize(path) / 1024, 1),
                })
    return render_template("digests.html", files=files)


@app.route("/digests/<filename>")
def serve_digest(filename):
    return send_from_directory(DIGEST_DIR, filename)


# ---------------------------------------------------------------------------
# CV Management Routes
# ---------------------------------------------------------------------------

@app.route("/cv")
def cv_page():
    cv_data = load_cv_data()
    return render_template("cv.html", cv_data=cv_data)


@app.route("/api/cv/upload", methods=["POST"])
def upload_cv():
    """Accept a CV file upload, parse it, and store cv_data.json."""
    if "cv_file" not in request.files:
        return jsonify({"ok": False, "error": "No file provided"}), 400

    f = request.files["cv_file"]
    filename = f.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    text = ""
    if ext == "pdf":
        try:
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(f.read())) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception as e:
            return jsonify({"ok": False, "error": f"PDF parsing failed: {e}"}), 400
    elif ext == "docx":
        try:
            import docx, io
            doc = docx.Document(io.BytesIO(f.read()))
            text = "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            return jsonify({"ok": False, "error": f"DOCX parsing failed: {e}"}), 400
    elif ext in ("txt", ""):
        text = f.read().decode("utf-8", errors="ignore")
    else:
        return jsonify({"ok": False, "error": f"Unsupported file type: {ext}. Use PDF, DOCX, or TXT."}), 400

    if not text.strip():
        return jsonify({"ok": False, "error": "Could not extract text from the file"}), 400

    cv_data = parse_cv_text(text)
    save_cv_data(cv_data)
    logger.info("CV uploaded: %d skills detected", len(cv_data["skills"]))

    return jsonify({
        "ok": True,
        "skills_count": len(cv_data["skills"]),
        "skills": cv_data["skills"],
    })


@app.route("/api/cv/rescore", methods=["POST"])
def rescore_jobs():
    """Re-score all jobs in the DB against the uploaded CV."""
    cv_data = load_cv_data()
    if not cv_data:
        return jsonify({"ok": False, "error": "No CV uploaded yet"}), 400

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT job_id, role, job_description FROM job_listings")
    jobs = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not jobs:
        return jsonify({"ok": True, "updated": 0, "message": "No jobs in database"})

    conn = get_connection()
    cursor = conn.cursor()
    updated = 0
    for job in jobs:
        score = cv_score(job, cv_data)
        cursor.execute(
            "UPDATE job_listings SET cv_score = ? WHERE job_id = ?",
            (score, job["job_id"]),
        )
        updated += 1
    conn.commit()
    conn.close()

    logger.info("Re-scored %d jobs against CV", updated)
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/jobs/<job_id>/gap-analysis")
def gap_analysis(job_id):
    """Return gap analysis for a specific job against the uploaded CV."""
    cv_data = load_cv_data()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM job_listings WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return jsonify({"ok": False, "error": "Job not found"}), 404

    job = dict(row)
    result = compute_gap_analysis(job, cv_data)
    return jsonify({"ok": True, **result})


def _extract_cv_text(file_storage) -> str:
    """
    Extract plain text from an uploaded CV file (PDF, DOCX, or TXT).
    Returns extracted text string, or raises ValueError with a user-friendly message.
    """
    filename = (file_storage.filename or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    data = file_storage.read()

    if ext == "pdf":
        try:
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception as e:
            raise ValueError(f"PDF parsing failed: {e}")
    elif ext == "docx":
        try:
            import docx, io
            doc = docx.Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            raise ValueError(f"DOCX parsing failed: {e}")
    elif ext == "txt":
        text = data.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported file type '.{ext}'. Use PDF, DOCX, or TXT.")

    if not text.strip():
        raise ValueError("Could not extract any text from the CV file.")
    return text


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------

@app.route("/reminders")
def reminders():
    """List all reminders and show create form."""
    from reminder_runner import load_reminders
    all_reminders = load_reminders()
    return render_template("reminders.html", reminders=all_reminders)


@app.route("/reminders/create", methods=["POST"])
def reminders_create():
    """Create a new reminder with a mandatory CV upload."""
    from reminder_runner import load_reminders, save_reminders
    name = request.form.get("name", "").strip()
    keyword = request.form.get("keyword", "").strip()
    email_addr = request.form.get("email", "").strip()
    if not name or not keyword or not email_addr:
        flash("Name, keyword, and email are required.", "error")
        return redirect(url_for("reminders"))
    try:
        min_score = max(0, min(100, int(request.form.get("min_score", 65))))
        max_jobs = max(1, min(50, int(request.form.get("max_jobs", 20))))
    except (ValueError, TypeError):
        flash("Score and max jobs must be numbers.", "error")
        return redirect(url_for("reminders"))

    cv_file = request.files.get("cv_file")
    if not cv_file or not cv_file.filename:
        flash("A CV/resume file is required to create a reminder.", "error")
        return redirect(url_for("reminders"))
    try:
        cv_text = _extract_cv_text(cv_file)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("reminders"))
    cv_data = parse_cv_text(cv_text)

    all_reminders = load_reminders()
    all_reminders.append({
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "keyword": keyword,
        "min_score": min_score,
        "max_jobs": max_jobs,
        "email": email_addr,
        "enabled": True,
        "last_sent": None,
        "cv_data": cv_data,
    })
    try:
        save_reminders(all_reminders)
    except OSError as e:
        flash(f"Failed to save reminder: {e}", "error")
        return redirect(url_for("reminders"))
    flash(f"Reminder '{name}' created with {len(cv_data['skills'])} CV skills detected.", "success")
    return redirect(url_for("reminders"))


@app.route("/reminders/<reminder_id>/delete", methods=["POST"])
def reminders_delete(reminder_id):
    """Delete a reminder by id."""
    from reminder_runner import load_reminders, save_reminders
    all_reminders = load_reminders()
    all_reminders = [r for r in all_reminders if r.get("id") != reminder_id]
    save_reminders(all_reminders)
    flash("Reminder deleted.", "success")
    return redirect(url_for("reminders"))


@app.route("/reminders/<reminder_id>/toggle", methods=["POST"])
def reminders_toggle(reminder_id):
    """Enable or disable a reminder without deleting it."""
    from reminder_runner import load_reminders, save_reminders
    all_reminders = load_reminders()
    for r in all_reminders:
        if r.get("id") == reminder_id:
            r["enabled"] = not r.get("enabled", True)
            break
    save_reminders(all_reminders)
    return redirect(url_for("reminders"))


@app.route("/reminders/<reminder_id>/send", methods=["POST"])
def reminders_send(reminder_id):
    """Manually trigger a single reminder right now."""
    from reminder_runner import load_reminders, save_reminders
    from email_notifier import send_job_email

    preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    gmail_address = preferences.get("gmail_address", "").strip()
    gmail_app_password = preferences.get("gmail_app_password", "").strip()
    if not gmail_address or not gmail_app_password:
        flash("Gmail credentials not configured. Add them in Settings first.", "error")
        return redirect(url_for("reminders"))

    all_reminders = load_reminders()
    reminder = next((r for r in all_reminders if r.get("id") == reminder_id), None)
    if not reminder:
        flash("Reminder not found.", "error")
        return redirect(url_for("reminders"))

    keyword = (reminder.get("keyword") or "").strip()
    min_score = max(0, min(100, int(reminder.get("min_score", 65))))
    max_jobs = max(1, min(50, int(reminder.get("max_jobs", 20))))
    recipient = (reminder.get("email") or "").strip()
    name = reminder.get("name", "Job Alert")

    from reminder_runner import score_jobs_for_cv_reminder
    jobs = score_jobs_for_cv_reminder(reminder)
    if not jobs:
        flash(f"No jobs found matching '{keyword}' with score ≥ {min_score}.", "error")
        return redirect(url_for("reminders"))

    alert_prefs = dict(preferences)
    alert_prefs["job_titles"] = [keyword]
    success = send_job_email(recipient, jobs, alert_prefs)
    if success:
        reminder["last_sent"] = datetime.now().isoformat()
        save_reminders(all_reminders)
        flash(f"Sent {len(jobs)} jobs for '{name}' to {recipient}.", "success")
    else:
        flash(f"Failed to send email to {recipient}. Check Gmail credentials in Settings.", "error")

    return redirect(url_for("reminders"))


@app.route("/reminders/<reminder_id>/update-cv", methods=["POST"])
def reminders_update_cv(reminder_id):
    """Replace the CV for an existing reminder."""
    from reminder_runner import load_reminders, save_reminders
    cv_file = request.files.get("cv_file")
    if not cv_file or not cv_file.filename:
        flash("Please select a CV file to upload.", "error")
        return redirect(url_for("reminders"))
    try:
        cv_text = _extract_cv_text(cv_file)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("reminders"))
    cv_data = parse_cv_text(cv_text)

    all_reminders = load_reminders()
    updated = False
    for r in all_reminders:
        if r.get("id") == reminder_id:
            r["cv_data"] = cv_data
            updated = True
            break
    if updated:
        try:
            save_reminders(all_reminders)
        except OSError as e:
            flash(f"Failed to save reminder: {e}", "error")
            return redirect(url_for("reminders"))
        flash(f"CV updated — {len(cv_data['skills'])} skills detected.", "success")
    else:
        flash("Reminder not found.", "error")
    return redirect(url_for("reminders"))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5001)
