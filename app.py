"""
app.py - Flask web UI for Job Search Agent.
Provides a browser-based interface for managing preferences, running the scraper,
viewing jobs, and browsing digests.
"""

import os
import uuid
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
    # Phase 1 multi-user helpers
    get_or_create_user, get_user_by_email,
    update_applied_status_user, update_job_notes_user, hide_job_user,
    bulk_set_user_cv_scores,
    get_comprehensive_stats_user, get_dashboard_insights_user,
    get_application_pipeline_stats_user_with_legacy_fallback,
    user_state_join_sql,
)
from scrapers import scrape_all_portals
from analyzer import analyze_jobs, generate_tailored_points, parse_nlp_query, parse_cv_text, cv_score, compute_gap_analysis, load_cv_data, save_cv_data, CV_DATA_PATH
from digest_generator import generate_digest, get_latest_digest, DIGEST_DIR
from email_notifier import send_job_email
from reminder_runner import run_reminders
from database import delete_old_jobs
from contact_scraper import enrich_jobs_with_contacts
from database import update_job_description
from telegram_notifier import send_telegram_alert, send_telegram_batch_summary
from telegram_bot import start_telegram_bot
from git_sync import sync_from_scrape

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

_IS_VERCEL = bool(os.environ.get("VERCEL"))

app = Flask(__name__)

_secret = os.environ.get("FLASK_SECRET")
if not _secret:
    if _IS_VERCEL:
        raise RuntimeError(
            "FLASK_SECRET env var is required in production. "
            "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
        )
    _secret = "dev-only-insecure-key-do-not-use-in-production"
app.secret_key = _secret

app.config["SESSION_COOKIE_SECURE"]   = _IS_VERCEL
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------
from flask_wtf.csrf import CSRFProtect, generate_csrf
csrf = CSRFProtect(app)

@app.after_request
def set_csrf_cookie(response):
    response.headers['X-CSRF-Token'] = generate_csrf()
    return response

# ---------------------------------------------------------------------------
# Auth setup
# ---------------------------------------------------------------------------
from authlib.integrations.flask_client import OAuth
from flask import session

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

_PUBLIC_ENDPOINTS = frozenset({
    'login', 'auth_google', 'auth_callback', 'auth_dev_login', 'static',
    'approve_outreach', 'skip_outreach', 'favicon',
})

@app.before_request
def require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if session.get('user') is None:
        if request.is_json or (request.path or '').startswith('/api/'):
            from flask import abort
            abort(401)
        return redirect(url_for('login', next=request.path))


def current_user_id():
    """
    Resolve the DB user id for the current session, creating the user row on
    first access. Returns None for unauthenticated routes. Callers that require
    a user should use require_user_id() which aborts with 401.
    """
    user = dict(session).get('user') or {}
    uid = user.get('id')
    if uid:
        try:
            return int(uid)
        except (TypeError, ValueError):
            pass
    email = (user.get('email') or '').strip()
    if not email:
        return None
    try:
        uid = get_or_create_user(email, user.get('name'), user.get('picture'))
    except Exception as e:
        logger.warning("get_or_create_user failed for %s: %s", email, e)
        return None
    # Cache on the session so we don't hit the DB on every request
    session['user'] = dict(user, id=uid)
    return uid


def require_user_id():
    """current_user_id() that aborts the request with 401 when no user."""
    uid = current_user_id()
    if not uid:
        from flask import abort
        abort(401)
    return uid
# Initialize the database on startup.
#
# init_db() is idempotent (CREATE TABLE IF NOT EXISTS) but issues ~10 DDL
# round-trips. On Vercel that's wasted latency on every cold start, since
# the schema is already provisioned. Skip it when SKIP_INIT_DB is set;
# keep running it locally so dev environments bootstrap fresh tables.
if not os.environ.get("SKIP_INIT_DB"):
    try:
        init_db()
    except Exception as e:
        logger.warning("Database init warning (may be expected on Vercel): %s", e)

# Per-request DB connection reuse — see database.get_connection() docstring.
from database import close_request_connection  # noqa: E402
app.teardown_appcontext(close_request_connection)

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
_scraper_stop_event = threading.Event()        # stop requested from UI "Stop" button
_scheduled_stop_event = threading.Event()      # stop requested for scheduled runs only
_is_scheduled_run = False                      # True when current run was triggered by scheduler

# ---------------------------------------------------------------------------
# AI agent run state
# ---------------------------------------------------------------------------
agent_status = {"running": False, "queued": 0, "error": None, "finished_at": None}
agent_lock = threading.Lock()

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
    global scraper_status, _is_scheduled_run
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
    _scraper_stop_event.clear()
    _scheduled_stop_event.clear()
    _is_scheduled_run = True
    logger.info("Scheduled daily pipeline run starting")
    _run_scraper_pipeline()

    # Run AI agent pipeline after scraping
    try:
        from agent.graph import run_agent_pipeline
        import json as _json
        prefs = load_preferences() or DEFAULT_PREFS.copy()
        with open(os.path.join(BASE_DIR, "config.json")) as f:
            _config = _json.load(f)
        run_agent_pipeline(prefs, _config)
    except Exception as e:
        logger.error("AI agent pipeline error in scheduler: %s", e)


_HR_EMAIL_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Claude")


def _load_hm():
    """Return the hiring_managers_search module (now bundled in the repo)."""
    import hiring_managers_search
    return hiring_managers_search


def _load_gmail():
    """Load send_gmail module from Documents/Claude via importlib."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("send_gmail",
                                         os.path.join(_HR_EMAIL_DIR, "send_gmail.py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_hr_email(reminder_id: str):
    """Send daily HR email for a specific reminder (called by scheduler or on-demand)."""
    from datetime import date as _date
    from reminder_runner import load_reminders, save_reminders

    all_reminders = load_reminders()
    reminder = next((r for r in all_reminders if r.get("id") == reminder_id), None)
    if not reminder:
        logger.error("HR email: reminder %s not found", reminder_id)
        return

    try:
        hm    = _load_hm()
        gmail = _load_gmail()

        cv_data       = reminder.get("cv_data") or {}
        skills        = cv_data.get("skills") or []
        raw_text      = (cv_data.get("raw_text") or "")[:500]
        role_keywords = [k.strip() for k in reminder.get("keyword", "").split(",") if k.strip()]
        location      = reminder.get("hr_location") or "India"
        recipient     = reminder.get("email", "")
        user_name     = reminder.get("name", "")

        sent     = hm.load_hr_sent(reminder_id)
        contacts = hm.get_new_hiring_managers(
            sent, role_keywords=role_keywords, skills=skills,
            location=location, target=5
        )

        today_str = _date.today().strftime("%A, %d %B %Y")
        body = (
            f"Hi! 👋\n\nHere is your daily hiring manager digest for {today_str}.\n\n"
            "Below are recruiters actively hiring for your target roles —\n"
            "none of these have been sent to you before.\n\n"
            "Reach out via LinkedIn today and track your responses.\n"
        ) + hm.format_hiring_section(contacts, user_name=user_name,
                                      user_summary=raw_text,
                                      role_keywords=role_keywords)

        subject = f"🎯 Daily Hiring Managers — {_date.today().strftime('%d %b %Y')} ({len(contacts)} new contacts)"
        gmail.send_email(to=recipient, subject=subject, body=body)
        if contacts:
            hm.update_hr_sent(reminder_id, contacts)

        # Update last_hr_sent timestamp
        for r in all_reminders:
            if r.get("id") == reminder_id:
                r["last_hr_sent"] = _date.today().isoformat()
                break
        save_reminders(all_reminders)
        logger.info("HR email sent for reminder %s (%d contacts) to %s", reminder_id, len(contacts), recipient)
    except Exception as e:
        logger.error("HR email failed for reminder %s: %s", reminder_id, e)


def _reschedule_hr_jobs():
    """
    Register UI-only placeholder jobs in APScheduler for the /api/scheduler/jobs
    display. Actual HR email execution is handled by the simple scheduler loop.
    """
    if not _scheduler:
        return
    from apscheduler.triggers.cron import CronTrigger
    from reminder_runner import load_reminders

    try:
        reminders = load_reminders()
    except Exception:
        return

    for r in reminders:
        rid    = r.get("id", "")
        job_id = f"hr_email_{rid}"
        try:
            enabled = r.get("hr_email_enabled", False)
            hour   = int(r.get("hr_email_hour") or 11)
            minute = int(r.get("hr_email_minute") or 0)

            if enabled:
                _scheduler.add_job(
                    lambda: None,   # UI display only — simple scheduler fires the real job
                    trigger=CronTrigger(hour=hour, minute=minute),
                    id=job_id,
                    name=f"HR email — {r.get('name', rid)} at {hour:02d}:{minute:02d}",
                    replace_existing=True,
                )
                logger.info("HR email scheduled for %s at %02d:%02d", r.get("name", rid), hour, minute)
            else:
                try:
                    _scheduler.remove_job(job_id)
                except Exception:
                    pass
        except Exception as e:
            logger.error("Failed to schedule HR email for reminder %s: %s", rid, e)


def _send_prd_email_job():
    """Scheduled job: generate today's PRD and send it via email."""
    try:
        from prd_generator import generate_daily_prd, build_prd_email_html
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        prefs = load_preferences()
        gmail_address = prefs.get("gmail_address", "").strip()
        gmail_app_password = prefs.get("gmail_app_password", "").strip()
        recipient = prefs.get("email", "").strip()

        if not gmail_address or not gmail_app_password:
            logger.warning("PRD email skipped — Gmail credentials not configured in Settings")
            return
        if not recipient:
            logger.warning("PRD email skipped — no recipient email in Settings")
            return

        prd = generate_daily_prd()
        html_body = build_prd_email_html(prd)
        subject = f"📋 Daily PRD: {prd['product']['name']} ({prd['date']})"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = gmail_address
        msg["To"] = recipient
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_address, gmail_app_password)
            server.sendmail(gmail_address, [recipient], msg.as_string())

        logger.info("PRD email sent to %s: %s", recipient, prd["product"]["name"])
    except Exception as e:
        logger.error("PRD email job failed: %s", e)


def _startup_catchup():
    """
    On startup, fire any missed daily emails if we're past their scheduled time
    and the mail hasn't been sent yet today. Runs in a background thread.
    """
    import threading, time as _time
    from datetime import datetime as _dt, date as _date

    def _run():
        _time.sleep(5)  # wait for scheduler to settle
        now = _dt.now()
        today_str = _date.today().isoformat()

        # ── PRD email: scheduled at 08:00, send if past 08:00 and no cache yet sent ──
        prd_cache = os.path.join(BASE_DIR, "data", "prds", f"prd_{today_str}.json")
        prd_sent_flag = os.path.join(BASE_DIR, "data", "prds", f"prd_{today_str}.sent")
        if now.hour >= 8 and not os.path.exists(prd_sent_flag):
            logger.info("Startup catch-up: PRD email not sent today — sending now")
            try:
                _send_prd_email_job()
                # Mark as sent
                from pathlib import Path as _Path
                _Path(prd_sent_flag).touch()
            except Exception as e:
                logger.error("Startup PRD catch-up failed: %s", e)
        else:
            logger.info("Startup catch-up: PRD already sent today or before 08:00 — skipping")

        # ── HR emails: scheduled at 11:00 ──
        if now.hour >= 11:
            from reminder_runner import load_reminders
            reminders = load_reminders()  # returns a list of dicts, each with "id" key
            for r in reminders:
                if not r.get("hr_email_enabled"):
                    continue
                rid = r.get("id", "")
                last_hr_sent = r.get("last_hr_sent", "")
                if last_hr_sent and last_hr_sent[:10] == today_str:
                    continue  # already sent today
                logger.info("Startup catch-up: HR email for '%s' not sent today — sending now", r.get("name", rid))
                try:
                    _run_hr_email(rid)
                except Exception as e:
                    logger.error("Startup HR catch-up failed for %s: %s", rid, e)
        else:
            logger.info("Startup catch-up: before 11:00 — HR emails will fire on schedule")

    threading.Thread(target=_run, daemon=True).start()


def setup_background_scheduler():
    """
    Start a simple time-checker scheduler. Replaces APScheduler to avoid
    executor-blocking issues caused by long-running scraper jobs.

    The scheduler loop wakes every 60 seconds, checks the current time, and
    fires jobs that haven't run yet today. Each job runs in its own daemon
    thread so the loop is never blocked.
    """
    global _scheduler

    # Keep APScheduler alive for the /api/scheduler/jobs endpoint compatibility
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.add_job(lambda: None, trigger=CronTrigger(hour=7, minute=0),
                           id="morning_pipeline",
                           name="Morning job scraper pipeline at 07:00",
                           replace_existing=True)
        _scheduler.add_job(lambda: None, trigger=CronTrigger(hour=19, minute=0),
                           id="evening_pipeline",
                           name="Evening job scraper pipeline at 19:00",
                           replace_existing=True)
        _scheduler.add_job(lambda: None, trigger=CronTrigger(hour=8, minute=0),
                           id="daily_prd_email",
                           name="Daily PRD email at 08:00",
                           replace_existing=True)
        _scheduler.start()
    except Exception:
        pass  # API compatibility only — actual scheduling is done below

    _start_simple_scheduler()


def _start_simple_scheduler():
    """
    Reliable minute-tick scheduler that fires jobs in isolated daemon threads.
    Cannot be blocked by long-running jobs.
    """
    import threading
    import time as _time
    from datetime import datetime as _dt, date as _date

    PRD_HOUR = 8
    HR_HOUR  = 11
    MORNING_PIPELINE_HOUR = 7
    EVENING_PIPELINE_HOUR = 19

    _SCHED_STATE_FILE = os.path.join(BASE_DIR, "data", "scheduler_state.json")

    def _load_state() -> dict:
        import json as _j
        try:
            with open(_SCHED_STATE_FILE) as f:
                return _j.load(f)
        except Exception:
            return {}

    def _save_state(state: dict):
        import json as _j
        try:
            os.makedirs(os.path.dirname(_SCHED_STATE_FILE), exist_ok=True)
            with open(_SCHED_STATE_FILE, "w") as f:
                _j.dump(state, f)
        except Exception as e:
            logger.warning("Scheduler state save failed: %s", e)

    _ran_today: dict = _load_state()   # persisted: job_key -> date string of last run

    def _already_ran(key: str, today: str) -> bool:
        return _ran_today.get(key) == today

    def _mark_ran(key: str, today: str):
        _ran_today[key] = today
        _save_state(_ran_today)

    def _fire(name: str, fn, *args):
        def _run():
            try:
                fn(*args)
            except Exception as e:
                logger.error("Simple scheduler job '%s' failed: %s", name, e)
        t = threading.Thread(target=_run, daemon=True, name=f"job-{name}")
        t.start()

    def _loop():
        logger.info("Simple scheduler started — PRD@08:00, HR@11:00, pipeline@07:00/19:00")
        # Run startup catch-up first
        _startup_catchup()

        while True:
            _time.sleep(60)
            try:
                now   = _dt.now()
                today = _date.today().isoformat()
                h     = now.hour

                # PRD email at 08:00
                if h >= PRD_HOUR and not _already_ran("prd", today):
                    prd_sent_flag = os.path.join(BASE_DIR, "data", "prds", f"prd_{today}.sent")
                    if not os.path.exists(prd_sent_flag):
                        logger.info("Simple scheduler: firing PRD email")
                        _mark_ran("prd", today)
                        def _prd_job(flag=prd_sent_flag):
                            _send_prd_email_job()
                            from pathlib import Path as _Path
                            _Path(flag).touch()
                        _fire("prd_email", _prd_job)
                    else:
                        _mark_ran("prd", today)  # already sent, mark to skip

                # HR emails at 11:00
                if h >= HR_HOUR and not _already_ran("hr", today):
                    _mark_ran("hr", today)
                    logger.info("Simple scheduler: firing HR emails")
                    def _hr_jobs(t=today):
                        from reminder_runner import load_reminders
                        for r in load_reminders():
                            if not r.get("hr_email_enabled"):
                                continue
                            rid = r.get("id", "")
                            last = r.get("last_hr_sent", "")
                            if last and last[:10] == t:
                                continue
                            try:
                                _run_hr_email(rid)
                            except Exception as e:
                                logger.error("HR email failed for %s: %s", rid, e)
                    _fire("hr_emails", _hr_jobs)

                # Morning pipeline at 07:00
                if h >= MORNING_PIPELINE_HOUR and h < EVENING_PIPELINE_HOUR and not _already_ran("morning_pipeline", today):
                    _mark_ran("morning_pipeline", today)
                    logger.info("Simple scheduler: firing morning pipeline")
                    _fire("morning_pipeline", _scheduled_pipeline_run)

                # Evening pipeline at 19:00
                if h >= EVENING_PIPELINE_HOUR and not _already_ran("evening_pipeline", today):
                    _mark_ran("evening_pipeline", today)
                    logger.info("Simple scheduler: firing evening pipeline")
                    _fire("evening_pipeline", _scheduled_pipeline_run)


            except Exception as e:
                logger.error("Simple scheduler loop error: %s", e)

    t = threading.Thread(target=_loop, daemon=True, name="simple-scheduler")
    t.start()


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

    prefs = load_preferences() or {}
    linkedin_email = prefs.get("linkedin_email", "").strip()
    linkedin_password = prefs.get("linkedin_password", "").strip()

    contacts = enrich_jobs_with_contacts(
        rows,
        linkedin_email=linkedin_email or None,
        linkedin_password=linkedin_password or None,
    )
    for jid, info in contacts.items():
        update_job_contacts(
            jid,
            info.get("poster_name", ""),
            info.get("poster_email", ""),
            info.get("poster_phone", ""),
            info.get("poster_linkedin", ""),
        )
        # Also update the job description if LinkedIn JSON-LD returned one
        jd = info.get("jd_text", "")
        if jd:
            update_job_description(jid, jd)


def _run_scraper_pipeline():
    """Run the full pipeline in a background thread."""
    global scraper_status, _is_scheduled_run

    def _stopped():
        # Scheduled runs are only stopped by the dedicated scheduled-stop event.
        # Manual UI runs are stopped by the regular stop event.
        if _is_scheduled_run:
            return _scheduled_stop_event.is_set()
        return _scraper_stop_event.is_set()

    def _mark_stopped():
        global _is_scheduled_run
        with scraper_lock:
            scraper_status["phase"] = "stopped"
            scraper_status["finished_at"] = datetime.now().isoformat()
            scraper_status["running"] = False
        _is_scheduled_run = False
        logger.info("Scraper stopped")

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
            stop_event=_scraper_stop_event,
        )

        if _stopped():
            _mark_stopped()
            return

        with scraper_lock:
            scraper_status["total_jobs"] = len(all_jobs)

        if not all_jobs:
            with scraper_lock:
                scraper_status["phase"] = "done"
                scraper_status["finished_at"] = datetime.now().isoformat()
                scraper_status["running"] = False
            return

        # Phase 2: Analyze
        if _stopped():
            _mark_stopped()
            return
        with scraper_lock:
            scraper_status["phase"] = "analyzing"

        qualified_jobs, all_analyzed = analyze_jobs(all_jobs, preferences, config)

        with scraper_lock:
            scraper_status["qualified_jobs"] = len(qualified_jobs)

        # Phase 3: Store
        with scraper_lock:
            scraper_status["phase"] = "storing"

        _cv_data = load_cv_data()
        for job in all_analyzed:
            job["job_id"] = generate_job_id(
                job["portal"], job["company"], job["role"], job.get("location", ""),
            )
            if _cv_data:
                job["cv_score"] = cv_score(job, _cv_data)
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

        _cv_data = load_cv_data()
        for job in all_analyzed:
            job["job_id"] = generate_job_id(
                job["portal"], job["company"], job["role"], job.get("location", ""),
            )
            if _cv_data:
                job["cv_score"] = cv_score(job, _cv_data)
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


def _git_pull_and_sync():
    """
    Periodically git pull and import any new scrape committed by GitHub Actions.
    Runs every 30 minutes in a background thread.
    """
    import subprocess
    import time as _time

    while True:
        _time.sleep(1800)  # wait 30 minutes between checks
        try:
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0 and "Already up to date." not in result.stdout:
                logger.info("git pull: %s", result.stdout.strip())
                sync_from_scrape(BASE_DIR, insert_jobs_bulk)
                # Auto-run agent pipeline so new jobs get scored and drafted immediately
                try:
                    import json as _json
                    from agent.graph import run_agent_pipeline
                    _prefs = load_preferences() or DEFAULT_PREFS.copy()
                    with open(os.path.join(BASE_DIR, "config.json")) as _f:
                        _cfg = _json.load(_f)
                    run_agent_pipeline(_prefs, _cfg)
                    logger.info("git sync: agent pipeline completed")
                except Exception as _ae:
                    logger.warning("git sync: agent pipeline error: %s", _ae)
            else:
                logger.debug("git pull: no new commits")
        except Exception as e:
            logger.warning("git pull sync failed: %s", e)


if _should_start_background_tasks():
    setup_background_scheduler()

    # Auto-import any scraped jobs committed by GitHub Actions (on startup)
    import threading as _threading
    _threading.Thread(
        target=sync_from_scrape,
        args=(BASE_DIR, insert_jobs_bulk),
        daemon=True,
    ).start()

    # Periodically pull latest scrape from GitHub Actions and import
    _threading.Thread(target=_git_pull_and_sync, daemon=True).start()

    # Start Telegram bot if token is configured
    _bot_prefs = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    _bot_token = _bot_prefs.get("telegram_bot_token", "").strip()
    if _bot_token:
        start_telegram_bot(_bot_token)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Auth Routes
# ---------------------------------------------------------------------------

@app.route('/login')
def login():
    if session.get('user'):
        return redirect(url_for('dashboard'))
    return render_template('login.html', is_vercel=_IS_VERCEL)

@app.route('/auth/google')
def auth_google():
    if not os.environ.get("GOOGLE_CLIENT_ID"):
        flash("Google OAuth is not configured.", "error")
        return redirect(url_for('login'))
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
@csrf.exempt
def auth_callback():
    try:
        token = google.authorize_access_token()
        userinfo = token.get('userinfo') or {}
        email = (userinfo.get('email') or '').strip().lower()
        if not email:
            flash("Authentication failed: no email returned.", "error")
            return redirect(url_for('login'))
        uid = get_or_create_user(email, userinfo.get('name', ''), userinfo.get('picture', ''))
        session.permanent = True
        session['user'] = {
            'email': email,
            'name':  userinfo.get('name', ''),
            'picture': userinfo.get('picture', ''),
            'id':    uid,
        }
    except Exception as e:
        logger.error("OAuth callback error: %s", e)
        flash("Authentication failed.", "error")
        return redirect(url_for('login'))
    next_url = request.args.get('next') or url_for('dashboard')
    # Reject external redirects (open-redirect fix)
    from urllib.parse import urlparse
    if urlparse(next_url).netloc:
        next_url = url_for('dashboard')
    return redirect(next_url)

@app.route('/auth/dev-login', methods=['POST'])
@csrf.exempt
def auth_dev_login():
    """Local dev only — not available when running on Vercel."""
    if _IS_VERCEL:
        from flask import abort
        abort(403)
    uid = get_or_create_user('dev@localhost', 'Dev User')
    session.permanent = True
    session['user'] = {'email': 'dev@localhost', 'name': 'Dev User', 'picture': '', 'id': uid}
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/")
def index():
    if session.get('user'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route("/favicon.ico")
def favicon():
    # Return 204 No Content to avoid 500 errors when favicon is missing
    return "", 204


@app.route("/dashboard")
def dashboard():
    """
    Trimmed dashboard: three stats (scraped today, applied today,
    high-match ≥80% today) and the top 10 best-matched jobs from the
    last 24 hours with apply links. Everything else removed.
    """
    uid = current_user_id()
    cv_data = load_cv_data(uid) if uid else None
    cv_uploaded = cv_data is not None
    user_email = dict(session).get('user', {}).get('email', '')

    # Keep lazy scoring fresh so today's numbers reflect today's scrape
    if uid and cv_uploaded:
        try:
            _score_unscored_for_user(uid, limit=300)
        except Exception as e:
            logger.warning("Dashboard lazy scoring failed for user %s: %s", uid, e)

    today_str = datetime.now().strftime("%Y-%m-%d")
    h12_ago = (datetime.now() - timedelta(hours=12)).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    # 1) Jobs scraped today (shared)
    cur.execute(
        "SELECT COUNT(*) AS n FROM job_listings WHERE date_found LIKE ?",
        (f"{today_str}%",),
    )
    jobs_today = cur.fetchone()["n"]

    # 2) Jobs the user marked Applied today
    applied_today = 0
    if uid:
        cur.execute(
            """
            SELECT COUNT(*) AS n FROM user_job_state
            WHERE user_id = ? AND applied_status = 1
              AND applied_date LIKE ?
            """,
            (uid, f"{today_str}%"),
        )
        applied_today = cur.fetchone()["n"]

    # 3) High-match (≥80) jobs found today, scored for this user
    high_match_today = 0
    if uid:
        cur.execute(
            """
            SELECT COUNT(*) AS n
            FROM user_job_state s
            JOIN job_listings j ON j.job_id = s.job_id
            WHERE s.user_id = ?
              AND j.date_found LIKE ?
              AND s.cv_score >= 80
            """,
            (uid, f"{today_str}%"),
        )
        high_match_today = cur.fetchone()["n"]

    # 4) Top 10 scored matches from the last 12 hours (aligns with twice-daily scrape cadence).
    #    Falls back to last 48 hours when the 12-hour window yields fewer than 3 results,
    #    so the dashboard is never misleadingly empty after a delayed scrape.
    top_jobs = []
    if uid and cv_uploaded:
        cur.execute(
            """
            SELECT j.job_id, j.role, j.company, j.location,
                   j.remote_status, j.salary, j.portal, j.apply_url,
                   j.date_found,
                   s.cv_score
            FROM job_listings j
            JOIN user_job_state s
              ON s.job_id = j.job_id AND s.user_id = ?
            WHERE j.date_found >= ?
              AND COALESCE(s.hidden, 0) = 0
              AND s.cv_score > 0
            ORDER BY s.cv_score DESC, j.date_found DESC
            LIMIT 10
            """,
            (uid, h12_ago),
        )
        top_jobs = [dict(r) for r in cur.fetchall()]
        if len(top_jobs) < 3:
            # Widen to 48 hours if the last 12-hour window is sparse
            h48_ago = (datetime.now() - timedelta(hours=48)).isoformat()
            cur.execute(
                """
                SELECT j.job_id, j.role, j.company, j.location,
                       j.remote_status, j.salary, j.portal, j.apply_url,
                       j.date_found,
                       s.cv_score
                FROM job_listings j
                JOIN user_job_state s
                  ON s.job_id = j.job_id AND s.user_id = ?
                WHERE j.date_found >= ?
                  AND COALESCE(s.hidden, 0) = 0
                  AND s.cv_score > 0
                ORDER BY s.cv_score DESC, j.date_found DESC
                LIMIT 10
                """,
                (uid, h48_ago),
            )
            top_jobs = [dict(r) for r in cur.fetchall()]
    conn.close()

    return render_template(
        "dashboard.html",
        jobs_today=jobs_today,
        applied_today=applied_today,
        high_match_today=high_match_today,
        top_jobs=top_jobs,
        cv_uploaded=cv_uploaded,
        user_email=user_email,
    )


@app.route("/api/dashboard/top-jobs")
def dashboard_top_jobs():
    """Return top 10 jobs by CV score (or relevance score if no CV)."""
    uid = current_user_id()
    cv_data = load_cv_data(uid) if uid else None
    conn = get_connection()
    c = conn.cursor()
    if uid and cv_data:
        c.execute("""
            SELECT j.job_id, j.role, j.company, j.location, j.relevance_score,
                   COALESCE(s.cv_score, 0) AS cv_score,
                   j.apply_url, j.date_found, j.portal, j.remote_status
            FROM job_listings j
            LEFT JOIN user_job_state s ON s.job_id = j.job_id AND s.user_id = ?
            WHERE COALESCE(s.cv_score, 0) > 0
              AND COALESCE(s.hidden, 0) = 0
            ORDER BY COALESCE(s.cv_score, 0) DESC, j.relevance_score DESC
            LIMIT 10
        """, (uid,))
    elif uid:
        c.execute("""
            SELECT j.job_id, j.role, j.company, j.location, j.relevance_score,
                   COALESCE(s.cv_score, 0) AS cv_score,
                   j.apply_url, j.date_found, j.portal, j.remote_status
            FROM job_listings j
            LEFT JOIN user_job_state s ON s.job_id = j.job_id AND s.user_id = ?
            WHERE COALESCE(s.hidden, 0) = 0
            ORDER BY j.relevance_score DESC, j.date_found DESC
            LIMIT 10
        """, (uid,))
    elif cv_data:
        c.execute("""
            SELECT job_id, role, company, location, relevance_score, cv_score,
                   apply_url, date_found, portal, remote_status
            FROM job_listings
            WHERE cv_score > 0 AND (hidden=0 OR hidden IS NULL)
            ORDER BY cv_score DESC, relevance_score DESC
            LIMIT 10
        """)
    else:
        c.execute("""
            SELECT job_id, role, company, location, relevance_score, cv_score,
                   apply_url, date_found, portal, remote_status
            FROM job_listings
            WHERE (hidden=0 OR hidden IS NULL)
            ORDER BY relevance_score DESC, date_found DESC
            LIMIT 10
        """)
    jobs = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({"jobs": jobs, "cv_uploaded": cv_data is not None})


@app.route("/api/dashboard/reminder", methods=["POST"])
def dashboard_create_reminder():
    """Quick-create a job alert reminder from the dashboard widget."""
    from reminder_runner import load_reminders, save_reminders
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    keyword = (data.get("keyword") or "").strip()
    if not email or not keyword:
        return jsonify({"ok": False, "error": "Email and keyword are required"}), 400

    cv_data = load_cv_data()
    all_reminders = load_reminders()
    # Update existing reminder for this email+keyword if present, otherwise create
    existing = next((r for r in all_reminders
                     if r.get("email") == email and r.get("type", "jobs") == "jobs"), None)
    if existing:
        existing["keyword"] = keyword
        existing["enabled"] = True
        if cv_data:
            existing["cv_data"] = cv_data
    else:
        import uuid
        all_reminders.append({
            "id": uuid.uuid4().hex,
            "name": f"Dashboard Alert — {email.split('@')[0]}",
            "keyword": keyword,
            "email": email,
            "min_score": 60,
            "max_jobs": 20,
            "enabled": True,
            "last_sent": None,
            "cv_data": cv_data or {},
        })
    save_reminders(all_reminders)
    return jsonify({"ok": True})


def _build_jobs_query(filters, user_id: int = None):
    """
    Build SQL WHERE clause and params from a filters dict.
    Returns (conditions, params, order, join_sql, join_params, select_extra).
    Callers compose:  SELECT <cols>, <select_extra> FROM job_listings <join_sql>
                      WHERE <AND of conditions> ORDER BY <order>
    `user_id` scopes per-user columns (applied_status / hidden / cv_score /
    notes) via a LEFT JOIN on user_job_state.
    """
    conditions = []
    params = []
    select_extra = ""

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
    company_stage = filters.get("company_stage", "")

    join_sql = ""
    join_params: list = []
    if user_id:
        join_sql = (
            " LEFT JOIN user_job_state ujs "
            "ON ujs.job_id = job_listings.job_id AND ujs.user_id = ?"
        )
        join_params = [int(user_id)]
        select_extra = (
            ", COALESCE(ujs.applied_status, 0) AS user_applied_status, "
            "COALESCE(ujs.cv_score, 0) AS user_cv_score, "
            "ujs.user_notes AS user_notes, "
            "ujs.applied_date AS user_applied_date, "
            "ujs.follow_up_date AS user_follow_up_date, "
            "ujs.rejection_reason AS user_rejection_reason, "
            "COALESCE(ujs.hidden, 0) AS user_hidden"
        )

    # Always exclude hidden jobs (hidden = 1) unless explicitly requested.
    # When user_id is in scope we honour the per-user hidden flag; otherwise
    # fall back to the legacy column on job_listings.
    if not filters.get("show_hidden"):
        if user_id:
            conditions.append("(COALESCE(ujs.hidden, 0) = 0)")
        else:
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
        min_score_val = 0
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
        if location == "Others":
            # Exclude the 4 pinned cities; show remaining non-null locations
            _pinned = ["Pune", "Mumbai", "Bangalore", "Hyderabad"]
            excl_patterns = []
            for city in _pinned:
                excl_patterns.extend(_CITY_PATTERNS.get(city, []))
            not_clauses = " AND ".join("location NOT LIKE ?" for _ in excl_patterns)
            conditions.append(
                f"(location IS NOT NULL AND location != '' AND ({not_clauses}))"
            )
            params.extend([f"%{p}%" for p in excl_patterns])
        else:
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

    if user_id:
        applied_map = {
            "none": "COALESCE(ujs.applied_status, 0) = 0",
            "applied": "ujs.applied_status = 1",
            "saved": "ujs.applied_status = 2",
            "phone_screen": "ujs.applied_status = 3",
            "interview": "ujs.applied_status = 4",
            "offer": "ujs.applied_status = 5",
            "rejected": "ujs.applied_status = 6",
        }
    else:
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

    if company_stage:
        conditions.append("company_funding_stage = ?")
        params.append(company_stage)

    if user_id:
        sort_map = {
            "score_desc": "relevance_score DESC, date_found DESC",
            "score_asc": "relevance_score ASC, date_found DESC",
            "date_desc": "date_found DESC",
            "date_asc": "date_found ASC",
            "company_asc": "company ASC",
            "cv_score_desc": "COALESCE(ujs.cv_score, 0) DESC, relevance_score DESC, date_found DESC",
            "cv_score_asc": "COALESCE(ujs.cv_score, 0) ASC, relevance_score ASC, date_found DESC",
        }
    else:
        sort_map = {
            "score_desc": "relevance_score DESC, date_found DESC",
            "score_asc": "relevance_score ASC, date_found DESC",
            "date_desc": "date_found DESC",
            "date_asc": "date_found ASC",
            "company_asc": "company ASC",
            "cv_score_desc": "cv_score DESC, relevance_score DESC, date_found DESC",
            "cv_score_asc": "cv_score ASC, relevance_score ASC, date_found DESC",
        }
    # When CV is uploaded, "score" sorts should use cv_score (the displayed score)
    if filters.get("cv_uploaded") and sort in ("score_desc", "score_asc"):
        sort = "cv_score_desc" if sort == "score_desc" else "cv_score_asc"
    order = sort_map.get(sort, "date_found DESC")

    return conditions, params, order, join_sql, join_params, select_extra


@app.route("/jobs")
def jobs():
    uid = current_user_id()
    cv_data = load_cv_data(uid) if uid else None
    cv_uploaded = cv_data is not None

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
        "company_stage": request.args.get("company_stage", ""),
        "cv_uploaded": cv_uploaded,
    }
    conditions, params, order, join_sql, join_params, select_extra = _build_jobs_query(filters, user_id=uid)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    conn = get_connection()
    cursor = conn.cursor()

    # Pagination params
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
    per_page = 25
    offset = (page - 1) * per_page

    # Fetch total count
    cursor.execute(
        f"SELECT COUNT(*) as count FROM job_listings{join_sql}{where}",
        join_params + params,
    )
    total = cursor.fetchone()["count"]

    # Fetch paginated matching jobs
    cursor.execute(
        f"SELECT job_listings.*{select_extra} FROM job_listings{join_sql}{where} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        join_params + params + [per_page, offset],
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    # Overlay per-user state onto the legacy columns so templates / clients
    # that still read job.applied_status / cv_score / user_notes keep working.
    if uid:
        for r in rows:
            if "user_applied_status" in r:
                r["applied_status"] = r.pop("user_applied_status")
            if "user_cv_score" in r:
                r["cv_score"] = r.pop("user_cv_score")
            if "user_notes" in r and r["user_notes"] is not None:
                pass  # column name already matches
            if "user_applied_date" in r:
                r["applied_date"] = r.pop("user_applied_date")
            if "user_follow_up_date" in r:
                r["follow_up_date"] = r.pop("user_follow_up_date")
            if "user_rejection_reason" in r:
                r["rejection_reason"] = r.pop("user_rejection_reason")
            if "user_hidden" in r:
                r["hidden"] = r.pop("user_hidden")

    # Attach inline gap data if CV is uploaded
    if cv_uploaded:
        # Dynamic missing skills analysis ONLY (score already pre-computed or we just rely on cv_score)
        for job in rows:
            gap = compute_gap_analysis(job, cv_data)
            job["_missing_top3"] = gap.get("missing_skills", [])[:3]
            # Since we're paginated, we rely on the db column `cv_score` for sorting
            # But let's also pass the computed score incase it changed
            job["_cv_score"] = gap.get("cv_score", job.get("cv_score", 0))
    else:
        for job in rows:
            job["_missing_top3"] = []
            job["_cv_score"] = 0

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("ajax") == "1":
        return render_template("_job_card_list.html", jobs=rows, cv_uploaded=cv_uploaded, offset=offset)

    # Get distinct portals for filter dropdown
    conn2 = get_connection()
    cur2 = conn2.cursor()
    cur2.execute("SELECT DISTINCT portal FROM job_listings ORDER BY portal")
    portals = [r["portal"] for r in cur2.fetchall()]
    conn2.close()

    _internal_keys = {"cv_uploaded", "show_hidden", "show_international"}
    clean_filters = {k: v for k, v in filters.items() if v and v != "0" and k not in _internal_keys}

    prefs = (load_preferences(uid) if uid else None) or DEFAULT_PREFS.copy()

    return render_template(
        "jobs.html",
        jobs=rows, total=total,
        portals=portals,
        filters=filters, clean_filters=clean_filters,
        cv_uploaded=cv_uploaded,
        prefs=prefs,
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

    uid = current_user_id()
    conditions, params, order, join_sql, join_params, select_extra = _build_jobs_query(filters, user_id=uid)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        f"SELECT COUNT(*) as cnt FROM job_listings{join_sql}{where}",
        join_params + params,
    )
    total = cursor.fetchone()["cnt"]

    cursor.execute(
        f"SELECT job_listings.*{select_extra} FROM job_listings{join_sql}{where} "
        f"ORDER BY {order} LIMIT 25",
        join_params + params,
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    if uid:
        for r in rows:
            if "user_applied_status" in r:
                r["applied_status"] = r.pop("user_applied_status")
            if "user_cv_score" in r:
                r["cv_score"] = r.pop("user_cv_score")
            if "user_hidden" in r:
                r["hidden"] = r.pop("user_hidden")

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


@app.route("/api/jobs", methods=["GET"])
def api_jobs_list():
    """
    Lightweight JSON list of jobs used by the /jobs page client-side render.

    Returns the freshest visible jobs joined with the current user's per-user
    state (cv_score, applied_status, hidden) so the UI can show personalised
    badges without re-running the heavy SQL builder.

    Query params:
        limit         int  (default 1000, max 5000)
        min_score     int  (default 0)
        all_locations bool (default 0) — set to 1 to bypass preference filter
    """
    uid = current_user_id()
    try:
        limit = max(1, min(5000, int(request.args.get("limit", 1000))))
    except (ValueError, TypeError):
        limit = 1000
    try:
        min_score = max(0, min(100, int(request.args.get("min_score", 0))))
    except (ValueError, TypeError):
        min_score = 0

    # Lazy per-user scoring — any jobs the user hasn't seen scored
    # (e.g. fresh from this morning's scrape) get scored now against
    # THIS user's CV + preferences. No-op when user has no CV.
    if uid:
        try:
            _score_unscored_for_user(uid, limit=limit)
        except Exception as e:
            logger.warning("Lazy scoring failed for user %s: %s", uid, e)

    # Build location filter from user preferences (skip if all_locations=1)
    loc_where = ""
    loc_params: list = []
    all_locations = request.args.get("all_locations", "0") not in ("", "0", "false")
    if uid and not all_locations:
        try:
            user_prefs = load_preferences(uid)
            pref_locs = [l.strip() for l in user_prefs.get("locations", []) if l.strip()]
        except Exception:
            pref_locs = []
        if pref_locs:
            loc_conds = []
            for loc in pref_locs:
                loc_lower = loc.lower()
                if loc_lower in ("remote", "wfh", "work from home", "anywhere"):
                    loc_conds.append("LOWER(COALESCE(j.remote_status, '')) LIKE '%remote%'")
                else:
                    patterns = _CITY_PATTERNS.get(loc, [loc_lower])
                    for p in patterns:
                        loc_conds.append("LOWER(j.location) LIKE ?")
                        loc_params.append(f"%{p}%")
            if loc_conds:
                loc_where = " AND (" + " OR ".join(loc_conds) + ")"

    # Work mode filter from user preferences
    mode_where = ""
    mode_params: list = []
    _all_modes = {"on-site", "remote", "hybrid"}
    if uid and not all_locations:
        try:
            work_modes = set(user_prefs.get("work_modes") or _all_modes)
        except Exception:
            work_modes = _all_modes
        if work_modes and work_modes != _all_modes:
            placeholders = ",".join("?" for _ in work_modes)
            mode_where = f" AND j.remote_status IN ({placeholders})"
            mode_params = list(work_modes)

    conn = get_connection()
    cursor = conn.cursor()
    if uid:
        cursor.execute(
            f"""
            SELECT j.job_id, j.role, j.company, j.location, j.salary,
                   j.salary_currency, j.remote_status, j.portal, j.apply_url,
                   j.job_description, j.relevance_score, j.date_found,
                   j.date_posted, j.experience_min, j.experience_max,
                   COALESCE(s.cv_score, 0)       AS cv_score,
                   COALESCE(s.applied_status, 0) AS applied_status,
                   s.applied_date                AS applied_date,
                   s.user_notes                  AS user_notes,
                   COALESCE(s.hidden, 0)         AS hidden
            FROM job_listings j
            LEFT JOIN user_job_state s
              ON s.job_id = j.job_id AND s.user_id = ?
            WHERE j.relevance_score >= ?
              AND COALESCE(s.hidden, 0) = 0
              {loc_where}{mode_where}
            ORDER BY j.date_found DESC
            LIMIT ?
            """,
            [uid, min_score] + loc_params + mode_params + [limit],
        )
    else:
        cursor.execute(
            """
            SELECT job_id, role, company, location, salary, salary_currency,
                   remote_status, portal, apply_url, job_description,
                   relevance_score, date_found, date_posted,
                   experience_min, experience_max,
                   COALESCE(cv_score, 0) AS cv_score,
                   COALESCE(applied_status, 0) AS applied_status,
                   applied_date, user_notes,
                   COALESCE(hidden, 0) AS hidden
            FROM job_listings
            WHERE relevance_score >= ?
              AND (hidden = 0 OR hidden IS NULL)
            ORDER BY date_found DESC
            LIMIT ?
            """,
            (min_score, limit),
        )
    jobs = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({"jobs": jobs, "count": len(jobs), "location_filtered": bool(loc_where), "mode_filtered": bool(mode_where)})


@app.route("/api/jobs/<job_id>/status", methods=["POST"])
def update_job_status(job_id):
    uid = require_user_id()
    data = request.get_json(silent=True) or {}
    status = data.get("status", 0)
    notes = data.get("notes")
    follow_up_date = data.get("follow_up_date")
    rejection_reason = data.get("rejection_reason")
    try:
        status = int(status)
    except (ValueError, TypeError):
        status = 0
    update_applied_status_user(uid, job_id, status, notes, follow_up_date, rejection_reason)
    return jsonify({"ok": True, "job_id": job_id, "status": status})


@app.route("/api/jobs/<job_id>/hide", methods=["POST"])
def hide_job_route(job_id):
    """Hide or unhide a job. Body: {hidden: true/false}"""
    uid = require_user_id()
    data = request.get_json(silent=True) or {}
    is_hidden = bool(data.get("hidden", True))
    hide_job_user(uid, job_id, is_hidden)
    return jsonify({"ok": True, "job_id": job_id, "hidden": is_hidden})


@app.route("/api/jobs/<job_id>/notes", methods=["POST"])
def save_job_notes(job_id):
    """Save user notes for a job without changing other fields."""
    uid = require_user_id()
    data = request.get_json(silent=True) or {}
    notes = data.get("notes", "")
    update_job_notes_user(uid, job_id, notes)
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/api/admin/dedup", methods=["POST"])
def admin_dedup():
    """Remove cross-portal duplicate jobs, keeping highest-scoring copy."""
    require_user_id()
    deleted = dedup_jobs()
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/preferences", methods=["GET", "POST"])
def preferences():
    """
    Simplified settings page: job preferences + account + sign-out only.
    The other fields (email creds, telegram, linkedin, apollo, agent
    config) still exist in storage — they're managed via .env / Vercel
    project env now, not via UI — so the POST handler MERGES form values
    into the stored prefs instead of replacing the whole dict.
    """
    if request.method == "POST":
        uid = current_user_id()
        existing = load_preferences(uid) or DEFAULT_PREFS.copy()
        updated = dict(existing)

        # Job preferences (the only fields this UI exposes)
        if "job_titles" in request.form:
            updated["job_titles"] = [
                t.strip() for t in request.form.get("job_titles", "").split(",") if t.strip()
            ]
        if "locations" in request.form:
            updated["locations"] = [
                l.strip() for l in request.form.get("locations", "").split(",") if l.strip()
            ]
        if "transferable_skills" in request.form:
            updated["transferable_skills"] = [
                s.strip() for s in request.form.get("transferable_skills", "").split(",") if s.strip()
            ]
        work_modes = request.form.getlist("work_modes")
        if work_modes:
            updated["work_modes"] = work_modes
        save_preferences(updated, user_id=uid)
        cv = load_cv_data(uid) if uid else None
        if uid and cv:
            try:
                _rescore_all_jobs(cv, user_id=uid, preferences=updated)
            except Exception as e:
                logger.warning("Post-prefs rescore failed for user %s: %s", uid, e)
        flash("Job preferences saved.", "success")
        return redirect(url_for("preferences"))

    prefs = load_preferences() or DEFAULT_PREFS.copy()
    if not prefs.get("transferable_skills"):
        prefs["transferable_skills"] = DEFAULT_PREFS["transferable_skills"]
    return render_template("preferences.html", prefs=prefs)


@app.route("/api/preferences", methods=["POST"])
def api_save_preferences():
    """AJAX endpoint used by the preferences modal on the Jobs page."""
    uid = current_user_id()
    if not uid:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    existing = load_preferences(uid) or DEFAULT_PREFS.copy()
    updated = dict(existing)
    if "job_titles" in data:
        updated["job_titles"] = [t.strip() for t in data["job_titles"] if t.strip()]
    if "locations" in data:
        updated["locations"] = [l.strip() for l in data["locations"] if l.strip()]
    if "work_modes" in data:
        updated["work_modes"] = data["work_modes"] or list({"on-site", "remote", "hybrid"})
    if "transferable_skills" in data:
        updated["transferable_skills"] = [s.strip() for s in data["transferable_skills"] if s.strip()]
    try:
        save_preferences(updated, user_id=uid)
    except Exception as e:
        logger.error("Failed to save preferences for user %s: %s", uid, e)
        return jsonify({"ok": False, "error": "Failed to save"}), 500
    cv = load_cv_data(uid) if uid else None
    if uid and cv:
        try:
            _rescore_all_jobs(cv, user_id=uid, preferences=updated)
        except Exception as e:
            logger.warning("Post-prefs rescore failed for user %s: %s", uid, e)
    return jsonify({"ok": True})


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
    return render_template("scraper.html", config=load_config(), is_vercel=_IS_VERCEL)


@app.route("/api/jobs/import", methods=["POST"])
@csrf.exempt
def import_jobs():
    """Accept scraped jobs from external sources (e.g. GitHub Actions)."""
    # Auth: check header first, before parsing body
    import_secret = os.environ.get("IMPORT_SECRET", "")
    provided = request.headers.get("X-Import-Secret", "")
    if not provided:
        # Fall back to body only after header check fails (backward compat)
        provided = (request.get_json(silent=True, force=True) or {}).get("secret", "")
    if import_secret and provided != import_secret:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}

    jobs = data.get("jobs")
    if not jobs or not isinstance(jobs, list):
        return jsonify({"ok": False, "error": "Missing or invalid 'jobs' array"}), 400

    # Generate job IDs and insert
    _cv_data = load_cv_data()
    for job in jobs:
        job["job_id"] = generate_job_id(
            job.get("portal", "unknown"),
            job.get("company", ""),
            job.get("role", ""),
            job.get("location", ""),
        )
        if _cv_data:
            job["cv_score"] = cv_score(job, _cv_data)
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


@app.route("/api/portals/update", methods=["POST"])
def update_portals():
    """Enable or disable job portals. Persists to config.json."""
    require_user_id()
    data = request.get_json(force=True) or {}
    enabled_portals = data.get("enabled", [])   # list of portal names to enable

    config_path = os.path.join(BASE_DIR, "config.json")
    try:
        with open(config_path) as f:
            config = json.load(f)
    except Exception:
        config = load_config()

    all_portal_names = list(config.get("portals", {}).keys())
    for name in all_portal_names:
        config["portals"][name]["enabled"] = (name in enabled_portals)

    # Atomic write — temp file then rename so a crash never corrupts config.json
    tmp_path = config_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, config_path)

    return jsonify({"ok": True, "enabled": enabled_portals})


@app.route("/api/scraper/start", methods=["POST"])
def start_scraper():
    require_user_id()
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
    _scraper_stop_event.clear()
    _is_scheduled_run = False
    t = threading.Thread(target=_run_scraper_pipeline, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/scraper/quick-start", methods=["POST"])
def quick_scraper():
    """
    Vercel-safe quick scrape — runs only HiringCafe, Remotive, and Hacker News.
    No Selenium required. Completes in ~20s. Available on both local and Vercel.
    """
    uid = require_user_id()
    from scrapers import VERCEL_SAFE_PORTALS

    config = load_config()
    preferences = apply_env_overrides(load_preferences(uid) or DEFAULT_PREFS.copy())
    job_titles = preferences.get("job_titles", ["Product Manager"])
    locations  = preferences.get("locations", ["India"])

    try:
        from scrapers import scrape_all_portals as _scrape
        all_jobs, portal_results = _scrape(
            job_titles, locations, config,
            allowed_portals=VERCEL_SAFE_PORTALS,
        )
    except Exception as e:
        logger.error("Quick scrape failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

    if not all_jobs:
        counts = {p: r["count"] for p, r in portal_results.items()}
        return jsonify({"ok": True, "inserted": 0, "skipped": 0, "portals": counts,
                        "message": "No new jobs found."})

    cv_data = load_cv_data(uid)
    for job in all_jobs:
        job["job_id"] = generate_job_id(
            job.get("portal", "unknown"),
            job.get("company", ""),
            job.get("role", ""),
            job.get("location", ""),
        )
        if cv_data:
            job["cv_score"] = cv_score(job, cv_data)

    inserted, skipped = insert_jobs_bulk(all_jobs)
    logger.info("Quick scrape: inserted=%d skipped=%d portals=%s", inserted, skipped,
                list(portal_results.keys()))
    counts = {p: r["count"] for p, r in portal_results.items()}
    return jsonify({"ok": True, "inserted": inserted, "skipped": skipped, "portals": counts})


@app.route("/api/scraper/trigger-github", methods=["POST"])
def trigger_github_scrape():
    """
    Dispatch the GitHub Actions scrape workflow via the GitHub REST API.
    Requires GITHUB_TOKEN (PAT with workflow scope) and GITHUB_REPO env vars.
    """
    require_user_id()
    import requests as _req

    token = os.environ.get("GITHUB_TOKEN", "")
    repo  = os.environ.get("GITHUB_REPO", "gmpro-cr/Job-Search-Agent")
    if not token:
        return jsonify({"ok": False,
                        "error": "GITHUB_TOKEN not set. Add a Personal Access Token "
                                 "with 'workflow' scope to your environment variables."}), 400

    url = f"https://api.github.com/repos/{repo}/actions/workflows/scrape.yml/dispatches"
    try:
        resp = _req.post(
            url,
            json={"ref": "main"},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"GitHub API request failed: {e}"}), 500

    if resp.status_code == 204:
        runs_url = f"https://github.com/{repo}/actions/workflows/scrape.yml"
        return jsonify({"ok": True, "runs_url": runs_url,
                        "message": "Scrape workflow triggered. Results arrive in ~10 minutes."})
    else:
        return jsonify({"ok": False, "error": f"GitHub API returned {resp.status_code}: {resp.text[:200]}"}), 500


@app.route("/api/scraper/stop", methods=["POST"])
def stop_scraper():
    """Stop a manually triggered scraper run. Does NOT affect scheduled runs."""
    global scraper_status
    with scraper_lock:
        if not scraper_status["running"]:
            return jsonify({"ok": False, "error": "Scraper is not running"}), 409
        if _is_scheduled_run:
            return jsonify({"ok": False, "error": "A scheduled run is in progress. Use Stop Scheduled Run to cancel it."}), 409
    _scraper_stop_event.set()
    with scraper_lock:
        scraper_status["phase"] = "stopping"
    return jsonify({"ok": True})


@app.route("/api/scraper/stop-scheduled", methods=["POST"])
def stop_scheduled_scraper():
    """Stop the currently running scheduled scraper run."""
    global scraper_status
    with scraper_lock:
        if not scraper_status["running"]:
            return jsonify({"ok": False, "error": "No scraper run in progress"}), 409
        if not _is_scheduled_run:
            return jsonify({"ok": False, "error": "No scheduled run in progress. Use Stop to cancel a manual run."}), 409
    _scheduled_stop_event.set()
    with scraper_lock:
        scraper_status["phase"] = "stopping"
    return jsonify({"ok": True})


@app.route("/api/scraper/status")
def scraper_status_api():
    with scraper_lock:
        data = dict(scraper_status)
    data["is_scheduled_run"] = _is_scheduled_run
    return jsonify(data)


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


def _load_all_hm_contacts() -> list:
    """
    Load all sent hiring-manager contacts from hr_sent_contacts.json,
    deduplicated by name, newest first. Works on both local and Vercel.
    """
    import json as _json
    # Try the local Documents/Claude path first, then DATA_DIR fallback
    candidates = [
        os.path.join(_HR_EMAIL_DIR, "hr_sent_contacts.json"),
        os.path.join(BASE_DIR, "data", "hr_sent_contacts.json"),
    ]
    data = {}
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as _f:
                    data = _json.load(_f)
                break
            except Exception:
                pass
    if not data:
        return []
    seen_names = set()
    all_entries = []
    for contacts in data.values():
        all_entries.extend(contacts)
    all_entries.sort(key=lambda x: x.get("date_sent", ""), reverse=True)
    result = []
    for c in all_entries:
        key = (c.get("name") or "").lower().strip()
        if key and key not in seen_names:
            seen_names.add(key)
            result.append(c)
    return result


@app.route("/hiring-managers")
def hiring_managers():
    """Recruiter/TA contacts found via LinkedIn search, sourced from hr_sent_contacts.json."""
    contacts = [c for c in _load_all_hm_contacts() if c.get("linkedin_url")]
    return render_template(
        "hiring_managers.html",
        contacts=contacts,
        total=len(contacts),
    )


@app.route("/api/hiring-managers/search", methods=["POST"])
def api_hiring_managers_search():
    """Run a fresh hiring manager search and store results (does not send email)."""
    from reminder_runner import load_reminders as _load_rem
    reminders = _load_rem() or []
    # Use the first hr_email_enabled reminder's config as search parameters
    reminder = next((r for r in reminders if r.get("hr_email_enabled") and r.get("email")), None)
    if not reminder:
        return jsonify({"ok": False, "error": "No active hiring manager reminder configured"}), 400
    try:
        hm = _load_hm()
        role_keywords = [k.strip() for k in reminder.get("keyword", "").split(",") if k.strip()]
        location = reminder.get("hr_location") or "India"
        cv_data_r = reminder.get("cv_data") or {}
        skills = cv_data_r.get("skills") or []
        sent = hm.load_hr_sent(reminder["id"])
        new_contacts = hm.get_new_hiring_managers(
            sent, role_keywords=role_keywords, skills=skills,
            location=location, target=10,
        )
        if new_contacts:
            hm.update_hr_sent(reminder["id"], new_contacts)
            # Also write to DATA_DIR fallback so Vercel can read it
            import json as _json
            fallback = os.path.join(BASE_DIR, "data", "hr_sent_contacts.json")
            try:
                if os.path.exists(fallback):
                    with open(fallback, encoding="utf-8") as _f:
                        existing = _json.load(_f)
                else:
                    existing = {}
            except Exception:
                existing = {}
            rid = reminder["id"]
            existing.setdefault(rid, [])
            for c in new_contacts:
                existing[rid].append({
                    "name": c["name"], "company": c["company"],
                    "their_role": c.get("their_role", ""),
                    "linkedin_url": c.get("linkedin_url", ""),
                    "date_sent": date.today().isoformat(),
                })
            os.makedirs(os.path.dirname(fallback), exist_ok=True)
            with open(fallback, "w", encoding="utf-8") as _f:
                _json.dump(existing, _f, indent=2)
        return jsonify({"ok": True, "found": len(new_contacts)})
    except Exception as e:
        logger.error("Hiring manager search failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/digests")
def digests():
    from digest_generator import list_digests
    files = []
    for entry in list_digests():
        try:
            dt = datetime.fromisoformat(entry["uploaded_at"].replace("Z", "+00:00"))
            pretty = dt.strftime("%B %d, %Y %I:%M %p")
        except Exception:
            pretty = entry["uploaded_at"]
        files.append({
            "filename": entry["filename"],
            "date": pretty,
            "size_kb": entry["size_kb"],
        })
    # Top jobs for the latest digest display
    prefs = load_preferences()
    top_n = prefs.get("top_jobs_per_digest", 5) if prefs else 5
    min_score = prefs.get("min_score", 65) if prefs else 65
    uid = current_user_id()
    conn = get_connection()
    cursor = conn.cursor()
    if uid:
        cursor.execute(
            """
            SELECT j.role, j.company, j.location, j.salary, s.cv_score AS score
            FROM job_listings j
            JOIN user_job_state s ON s.job_id = j.job_id AND s.user_id = ?
            WHERE s.cv_score >= ?
            ORDER BY s.cv_score DESC
            LIMIT ?
            """,
            (uid, min_score, top_n),
        )
    else:
        cursor.execute(
            """SELECT role, company, location, salary, cv_score as score
               FROM job_listings WHERE cv_score >= ? ORDER BY cv_score DESC LIMIT ?""",
            (min_score, top_n),
        )
    top_jobs = [dict(r) for r in cursor.fetchall()]
    cursor.execute("SELECT COUNT(*) as total FROM job_listings")
    total = cursor.fetchone()["total"]
    if uid:
        cursor.execute(
            "SELECT COUNT(*) as q FROM user_job_state WHERE user_id = ? AND cv_score >= ?",
            (uid, min_score),
        )
        qualified = cursor.fetchone()["q"]
        cursor.execute(
            "SELECT AVG(cv_score) as avg FROM user_job_state WHERE user_id = ? AND cv_score > 0",
            (uid,),
        )
    else:
        cursor.execute("SELECT COUNT(*) as q FROM job_listings WHERE cv_score >= ?", (min_score,))
        qualified = cursor.fetchone()["q"]
        cursor.execute("SELECT AVG(cv_score) as avg FROM job_listings WHERE cv_score > 0")
    avg_row = cursor.fetchone()
    avg_score = round(avg_row["avg"] or 0, 1)
    conn.close()
    stats = {"total": total, "qualified": qualified, "avg_score": avg_score}
    user_email = dict(session).get('user', {}).get('email', '')

    from reminder_runner import load_reminders as _load_rem
    import glob as _glob
    all_reminders = _load_rem() or []

    # 1. Job alert emails (reminder sends matched job listings)
    job_alerts = [
        {
            "id": r.get("id"),
            "alert_type": "job_alert",
            "name": r.get("name") or r.get("keyword", ""),
            "email": r.get("email", ""),
            "keyword": r.get("keyword", ""),
            "last_sent": (r.get("last_sent") or "Never")[:10],
            "schedule": "Daily after each scrape",
            "min_score": r.get("min_score", 30),
            "max_jobs": r.get("max_jobs", 20),
        }
        for r in all_reminders
        if r.get("enabled") and r.get("email")
    ]

    # 2. Hiring manager emails (hr_email_enabled reminders)
    hr_alerts = [
        {
            "id": r.get("id"),
            "alert_type": "hiring_manager",
            "name": r.get("name") or r.get("keyword", ""),
            "email": r.get("email", ""),
            "keyword": r.get("keyword", ""),
            "last_sent": (r.get("last_hr_sent") or "Never")[:10],
            "schedule": f"Daily at {r.get('hr_email_hour', 11):02d}:00",
            "min_score": r.get("min_score", 30),
            "max_jobs": r.get("max_jobs", 20),
        }
        for r in all_reminders
        if r.get("hr_email_enabled") and r.get("email")
    ]

    # 3. PRD email (from preferences)
    prd_sent_files = sorted(_glob.glob(
        os.path.join(BASE_DIR, "data", "prds", "prd_*.sent")
    ))
    prd_last_sent = os.path.basename(prd_sent_files[-1]).replace("prd_", "").replace(".sent", "") if prd_sent_files else "Never"
    prd_recipient = (prefs or {}).get("email", "") or user_email
    prd_alerts = [{
        "id": "__prd__",
        "alert_type": "prd",
        "name": "Daily PRD",
        "email": prd_recipient,
        "keyword": "AI-generated product brief for your top role",
        "last_sent": prd_last_sent,
        "schedule": "Daily at 08:00",
        "min_score": None,
        "max_jobs": None,
    }] if prd_recipient else []

    all_alerts = job_alerts + hr_alerts + prd_alerts

    return render_template("digests.html", files=files, stats=stats, prefs=prefs,
                           user_email=user_email, job_alerts=all_alerts)


@app.route("/api/digest/settings", methods=["POST"])
def api_digest_settings():
    """Save digest-specific preferences (email, top_n, min_score)."""
    uid = current_user_id()
    if not uid:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    existing = load_preferences(uid) or DEFAULT_PREFS.copy()
    updated = dict(existing)
    if "email" in data:
        updated["email"] = (data["email"] or "").strip()
    if "top_jobs_per_digest" in data:
        try:
            updated["top_jobs_per_digest"] = max(1, min(50, int(data["top_jobs_per_digest"])))
        except (ValueError, TypeError):
            pass
    if "min_score" in data:
        try:
            updated["min_score"] = max(0, min(100, int(data["min_score"])))
        except (ValueError, TypeError):
            pass
    try:
        save_preferences(updated, user_id=uid)
    except Exception as e:
        logger.error("Failed to save digest settings for user %s: %s", uid, e)
        return jsonify({"ok": False, "error": "Failed to save"}), 500
    return jsonify({"ok": True})


@app.route("/api/digest/deactivate", methods=["POST"])
def api_digest_deactivate():
    """Clear the digest email so no more digests are sent."""
    uid = current_user_id()
    if not uid:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    existing = load_preferences(uid) or DEFAULT_PREFS.copy()
    updated = dict(existing)
    updated["email"] = ""
    try:
        save_preferences(updated, user_id=uid)
    except Exception as e:
        logger.error("Failed to deactivate digest for user %s: %s", uid, e)
        return jsonify({"ok": False, "error": "Failed to save"}), 500
    return jsonify({"ok": True})


@app.route("/api/digest/archive/<filename>", methods=["DELETE"])
def api_digest_archive_delete(filename):
    """Delete a single digest archive file from Blob / local FS."""
    uid = current_user_id()
    if not uid:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    if "/" in filename or ".." in filename or not filename.endswith(".html"):
        return jsonify({"ok": False, "error": "Invalid filename"}), 400
    from blob_storage import delete as blob_delete
    try:
        blob_delete(f"digests/{filename}")
    except Exception as e:
        logger.error("Failed to delete digest %s: %s", filename, e)
        return jsonify({"ok": False, "error": "Delete failed"}), 500
    return jsonify({"ok": True})


@app.route("/digests/<filename>")
def serve_digest(filename):
    from digest_generator import read_digest
    # Defence in depth: don't let a crafted filename escape the digest namespace.
    if "/" in filename or ".." in filename:
        return "Bad filename", 400
    data, content_type = read_digest(filename)
    if data is None:
        return "Digest not found", 404
    from flask import Response
    # Pass `content_type` (not `mimetype`) so Flask doesn't append a second
    # `; charset=utf-8` onto a mimetype string that already includes one.
    return Response(data, content_type=content_type)


# ---------------------------------------------------------------------------
# CV Management Routes
# ---------------------------------------------------------------------------

@app.route("/cv", methods=["GET", "POST"])
def cv_page():
    if request.method == "POST":
        if "cv_file" not in request.files:
            flash("No file selected.", "error")
            return redirect(url_for("cv_page"))
        f = request.files["cv_file"]
        if not f.filename:
            flash("No file selected.", "error")
            return redirect(url_for("cv_page"))
        filename = f.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        text = ""
        if ext == "pdf":
            try:
                import pdfplumber, io as _io
                with pdfplumber.open(_io.BytesIO(f.read())) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            except Exception as e:
                flash(f"PDF parsing failed: {e}", "error")
                return redirect(url_for("cv_page"))
        elif ext == "docx":
            try:
                import docx as _docx, io as _io
                doc = _docx.Document(_io.BytesIO(f.read()))
                text = "\n".join(p.text for p in doc.paragraphs)
            except Exception as e:
                flash(f"DOCX parsing failed: {e}", "error")
                return redirect(url_for("cv_page"))
        else:
            text = f.read().decode("utf-8", errors="ignore")
        cv_data = parse_cv_text(text)
        cv_data["filename"] = filename
        save_cv_data(cv_data)
        # Auto-populate any empty preference fields (titles / locations /
        # skills) from the parsed CV so first-time users land on a
        # working dashboard without filling forms.
        merged_prefs = _autofill_prefs_from_cv(cv_data)
        _uid = current_user_id()
        threading.Thread(
            target=_rescore_in_background,
            args=(cv_data, merged_prefs, _uid),
            daemon=True,
        ).start()
        flash(f"CV uploaded — {len(cv_data['skills'])} skills detected. Scoring jobs in the background…", "success")
        _next = request.form.get("next", "")
        from urllib.parse import urlparse as _urlparse
        if not _next or _urlparse(_next).netloc:
            _next = url_for("cv_page")
        return redirect(_next)

    cv_data = load_cv_data()
    uid = current_user_id()

    cv_filename = cv_data.get("filename") if cv_data else None
    cv_uploaded_at = cv_data.get("uploaded_at") if cv_data else None
    skills = cv_data.get("skills", []) if cv_data else []
    cv_skills_count = len(skills)
    skill_lower = [s.lower() for s in skills]

    conn = get_connection()
    cur = conn.cursor()

    # Total job count for demand %
    cur.execute("SELECT COUNT(*) as n FROM job_listings")
    total_job_count = max(cur.fetchone()["n"] or 1, 1)

    # Single JD scan
    cur.execute("SELECT job_description FROM job_listings LIMIT 3000")
    all_jds = " ".join((r["job_description"] or "") for r in cur.fetchall()).lower()

    # Skill demand: CV skills sorted by frequency in job market
    skill_demand = []
    for sk in skills[:14]:
        freq = all_jds.count(sk.lower())
        pct = round(freq / total_job_count * 100)
        skill_demand.append({"name": sk, "frequency": freq, "pct": min(100, pct)})
    skill_demand.sort(key=lambda x: -x["frequency"])

    # Skill gaps: broad domain-relevant list, not in CV
    _common = [
        "Machine Learning", "Generative AI", "LLMs", "Product Strategy", "Data Analysis",
        "SQL", "Python", "Stakeholder Management", "Agile", "Scrum", "AWS", "Docker",
        "System Design", "API Design", "User Research", "A/B Testing", "Product Analytics",
        "Roadmapping", "Go-to-Market", "Growth", "NLP", "React", "Kubernetes",
        "TypeScript", "Figma", "OKRs", "PRD Writing", "Leadership", "B2B SaaS",
    ]
    skill_gaps = []
    for sk in _common:
        if sk.lower() not in skill_lower:
            freq = all_jds.count(sk.lower())
            pct = round(freq / total_job_count * 100)
            if freq > 0:
                skill_gaps.append({"skill": sk, "demand_count": freq, "pct": min(100, pct)})
    skill_gaps.sort(key=lambda x: -x["demand_count"])
    skill_gaps = skill_gaps[:8]

    # Score distribution across all user-scored jobs
    score_dist_data = [0, 0, 0, 0, 0]  # 0-20, 21-40, 41-60, 61-80, 81-100
    total_scored = 0
    avg_match_score = 0
    if uid:
        cur.execute(
            "SELECT cv_score FROM user_job_state WHERE user_id = ? AND cv_score > 0",
            (uid,),
        )
        _scores = [r["cv_score"] for r in cur.fetchall()]
        total_scored = len(_scores)
        if _scores:
            avg_match_score = round(sum(_scores) / len(_scores))
            for sc in _scores:
                if sc <= 20:   score_dist_data[0] += 1
                elif sc <= 40: score_dist_data[1] += 1
                elif sc <= 60: score_dist_data[2] += 1
                elif sc <= 80: score_dist_data[3] += 1
                else:          score_dist_data[4] += 1

    # Portal breakdown (avg score + count)
    portal_breakdown = []
    if uid:
        cur.execute(
            """
            SELECT j.portal, COUNT(*) as cnt, AVG(s.cv_score) as avg_score
            FROM user_job_state s JOIN job_listings j ON j.job_id = s.job_id
            WHERE s.user_id = ? AND s.cv_score > 0
              AND j.portal IS NOT NULL AND j.portal != ''
            GROUP BY j.portal ORDER BY avg_score DESC LIMIT 6
            """,
            (uid,),
        )
        portal_breakdown = [
            {"portal": r["portal"], "count": r["cnt"],
             "avg_score": round(float(r["avg_score"] or 0))}
            for r in cur.fetchall()
        ]

    # Top matched job titles (cv_score >= 65)
    top_titles = []
    if uid:
        cur.execute(
            """
            SELECT j.role, COUNT(*) as cnt FROM user_job_state s
            JOIN job_listings j ON j.job_id = s.job_id
            WHERE s.user_id = ? AND s.cv_score >= 65
              AND j.role IS NOT NULL AND j.role != ''
            GROUP BY j.role ORDER BY cnt DESC LIMIT 8
            """,
            (uid,),
        )
        top_titles = [{"title": r["role"], "count": r["cnt"]} for r in cur.fetchall()]

    conn.close()

    # Skills tag cloud: (name, demand_pct) sorted by demand
    tag_cloud = sorted(
        [(sk, round(all_jds.count(sk.lower()) / total_job_count * 100)) for sk in skills[:24]],
        key=lambda x: -x[1],
    )

    return render_template("cv.html",
        cv_data=cv_data,
        cv_filename=cv_filename,
        cv_uploaded_at=cv_uploaded_at,
        cv_skills_count=cv_skills_count,
        skill_demand=skill_demand,
        skill_gaps=skill_gaps,
        score_dist_data=score_dist_data,
        total_scored=total_scored,
        avg_match_score=avg_match_score,
        portal_breakdown=portal_breakdown,
        top_titles=top_titles,
        tag_cloud=tag_cloud,
    )


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

    # Auto-fill preferences from the CV (only empty fields), then rescore
    # everything against the merged CV + prefs.
    merged_prefs = _autofill_prefs_from_cv(cv_data)
    updated = _rescore_all_jobs(cv_data, preferences=merged_prefs)

    return jsonify({
        "ok": True,
        "skills_count": len(cv_data["skills"]),
        "skills": cv_data["skills"],
        "suggested_job_titles": cv_data.get("suggested_job_titles", []),
        "suggested_locations":  cv_data.get("suggested_locations", []),
        "rescored": updated,
    })


def _rescore_in_background(cv_data, preferences, user_id):
    try:
        _rescore_all_jobs(cv_data, preferences=preferences, user_id=user_id)
    except Exception as e:
        logger.error("Background rescore failed for user %s: %s", user_id, e)


def _rescore_all_jobs(cv_data, user_id: int = None, preferences: dict = None):
    """
    Score every job in the DB against cv_data + preferences and persist
    into user_job_state (per-user). When user_id is omitted falls back
    to the legacy global column.
    """
    if user_id is None:
        user_id = current_user_id()
    if preferences is None and user_id:
        preferences = load_preferences(user_id) or {}
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT job_id, role, company, location, job_description, remote_status "
        "FROM job_listings"
    )
    jobs = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not jobs:
        return 0

    if user_id:
        scores = {job["job_id"]: cv_score(job, cv_data, preferences) for job in jobs}
        bulk_set_user_cv_scores(user_id, scores)
        logger.info("Re-scored %d jobs against CV+prefs for user %s", len(jobs), user_id)
        return len(jobs)

    conn = get_connection()
    cursor = conn.cursor()
    for job in jobs:
        score = cv_score(job, cv_data)
        cursor.execute(
            "UPDATE job_listings SET cv_score = ? WHERE job_id = ?",
            (score, job["job_id"]),
        )
    conn.commit()
    conn.close()
    logger.info("Re-scored %d jobs against CV (legacy global)", len(jobs))
    return len(jobs)


def _autofill_prefs_from_cv(cv_data: dict, user_id: int = None) -> dict:
    """
    After CV parse, fill any empty preference fields with what we
    inferred from the CV. Never overwrites a field the user has
    already populated — purely additive.
    Returns the merged preferences dict that was saved.
    """
    if user_id is None:
        user_id = current_user_id()
    if not user_id or not cv_data:
        return {}
    existing = load_preferences(user_id) or {}
    merged = dict(existing)

    suggestions = {
        "job_titles":          cv_data.get("suggested_job_titles") or [],
        "locations":           cv_data.get("suggested_locations") or [],
        "transferable_skills": cv_data.get("skills") or [],
    }
    changed = False
    for key, suggestion in suggestions.items():
        existing_list = merged.get(key) or []
        if not existing_list and suggestion:
            # Keep the order, take up to 6 to avoid bloating the form.
            merged[key] = suggestion[:6]
            changed = True

    if changed:
        save_preferences(merged, user_id=user_id)
        logger.info("Auto-filled prefs from CV for user %s: %s",
                    user_id,
                    {k: v for k, v in merged.items()
                     if k in ("job_titles", "locations", "transferable_skills")})
    return merged


def _score_unscored_for_user(user_id: int, limit: int = 200) -> int:
    """
    Lazy per-user scorer. On each /api/jobs hit (or wherever called),
    find jobs that don't yet have a user_job_state row for this user,
    score them against the user's CV + preferences, and bulk-write
    the resulting cv_scores.

    No-op if the user has no CV uploaded — without a CV every score
    would be 0 and we'd just be writing zero rows.

    Returns: number of jobs scored.
    """
    if not user_id:
        return 0
    cv_data = load_cv_data(user_id)
    if not cv_data:
        return 0  # nothing to score against
    from database import get_unscored_jobs_for_user
    jobs = get_unscored_jobs_for_user(user_id, limit=limit)
    if not jobs:
        return 0
    preferences = load_preferences(user_id) or {}
    scores = {j["job_id"]: cv_score(j, cv_data, preferences) for j in jobs}
    bulk_set_user_cv_scores(user_id, scores)
    logger.info("Lazy-scored %d new jobs for user %s", len(jobs), user_id)
    return len(jobs)


@app.route("/api/cv/skills")
def api_cv_skills():
    """Return parsed CV skills and raw text."""
    cv_data = load_cv_data()
    if not cv_data:
        return jsonify({"ok": False, "error": "CV not uploaded yet"}), 400
    return jsonify({"ok": True, "skills": cv_data.get("skills", []), "raw_text": cv_data.get("raw_text", "")})

@app.route("/api/cv/top_jobs")
def api_top_jobs():
    """Return top 10 jobs matched to the uploaded CV, sorted by cv_score descending."""
    uid = current_user_id()
    cv_data = load_cv_data()
    if not cv_data:
        return jsonify({"ok": False, "error": "CV not uploaded yet"}), 400
    conn = get_connection()
    cursor = conn.cursor()
    if uid:
        cursor.execute(
            """
            SELECT j.job_id, j.role, j.company, j.apply_url,
                   COALESCE(s.cv_score, 0) AS cv_score
            FROM job_listings j
            JOIN user_job_state s ON s.job_id = j.job_id AND s.user_id = ?
            WHERE s.cv_score IS NOT NULL AND s.cv_score > 0
            ORDER BY s.cv_score DESC
            LIMIT 10
            """,
            (uid,),
        )
    else:
        cursor.execute(
            "SELECT job_id, role, company, apply_url, cv_score FROM job_listings "
            "WHERE cv_score IS NOT NULL ORDER BY cv_score DESC LIMIT 10"
        )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({"ok": True, "jobs": rows})

@app.route("/api/reminder/set", methods=["POST"])
def api_set_reminder():
    """Create a reminder to email CV and top jobs at a specified datetime (ISO format)."""
    data = request.get_json() or {}
    email = data.get("email", "").strip()
    remind_at = data.get("remind_at", "")
    if not email or not remind_at:
        return jsonify({"ok": False, "error": "Missing email or remind_at"}), 400
    cv_data = load_cv_data()
    if not cv_data:
        return jsonify({"ok": False, "error": "CV not uploaded yet"}), 400
    # Load existing reminders
    from reminder_runner import load_reminders, save_reminders
    reminders = load_reminders()
    new_reminder = {
        "id": str(uuid.uuid4()),
        "type": "jobs",
        "email": email,
        "remind_at": remind_at,
        "cv_data": cv_data,
        "enabled": True,
        "keyword": "",
        "min_score": 65,
        "max_jobs": 20,
        "sent_job_ids": []
    }
    reminders.append(new_reminder)
    save_reminders(reminders)
    return jsonify({"ok": True, "reminder_id": new_reminder["id"]})

@app.route("/api/reminder/<reminder_id>", methods=["PATCH"])
def api_update_reminder(reminder_id):
    """Update editable fields on an existing reminder."""
    from reminder_runner import load_reminders, save_reminders
    data = request.get_json(silent=True) or {}
    reminders = load_reminders() or []
    target = next((r for r in reminders if r.get("id") == reminder_id), None)
    if not target:
        return jsonify({"ok": False, "error": "Reminder not found"}), 404
    if "email" in data:
        target["email"] = (data["email"] or "").strip()
    if "keyword" in data:
        target["keyword"] = (data["keyword"] or "").strip()
    if "name" in data:
        target["name"] = (data["name"] or "").strip()
    if "min_score" in data:
        try:
            target["min_score"] = max(0, min(100, int(data["min_score"])))
        except (ValueError, TypeError):
            pass
    if "max_jobs" in data:
        try:
            target["max_jobs"] = max(1, min(100, int(data["max_jobs"])))
        except (ValueError, TypeError):
            pass
    try:
        save_reminders(reminders)
    except Exception as e:
        logger.error("Failed to update reminder %s: %s", reminder_id, e)
        return jsonify({"ok": False, "error": "Save failed"}), 500
    return jsonify({"ok": True})


@app.route("/api/cv/rescore", methods=["POST"])
def rescore_jobs():
    """Re-score all jobs in the DB against the uploaded CV."""
    cv_data = load_cv_data()
    if not cv_data:
        return jsonify({"ok": False, "error": "No CV uploaded yet"}), 400
    updated = _rescore_all_jobs(cv_data)
    if updated == 0:
        return jsonify({"ok": True, "updated": 0, "message": "No jobs in database"})
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/cv/skills-gap")
def cv_skills_gap():
    """Return skill frequency across target-role jobs vs CV skills."""
    cv_data = load_cv_data()
    # If no CV data, redirect to upload page
    if not cv_data:
        return redirect(url_for('cv_page'))
    preferences = load_preferences() or DEFAULT_PREFS.copy()
    job_titles = preferences.get("job_titles", [])

    from database import get_skill_frequency
    skill_freq = get_skill_frequency(job_titles)

    cv_skills_lower = {s.lower() for s in (cv_data or {}).get("skills", [])}
    result = []
    for item in skill_freq:
        result.append({
            "skill": item["skill"],
            "count": item["count"],
            "pct": item["pct"],
            "in_cv": item["skill"].lower() in cv_skills_lower,
        })
    return jsonify({"ok": True, "skills": result, "cv_uploaded": cv_data is not None})


@app.route("/api/cv/keyword-heatmap")
def cv_keyword_heatmap():
    """Return top keywords across target-role jobs."""
    preferences = load_preferences() or DEFAULT_PREFS.copy()
    job_titles = preferences.get("job_titles", [])

    from database import get_keyword_frequency
    keywords = get_keyword_frequency(job_titles)
    return jsonify({"ok": True, "keywords": keywords})


@app.route("/api/cv/profile-score")
def cv_profile_score():
    """Return a simple profile completeness score 0-100."""
    cv_data = load_cv_data()
    preferences = load_preferences() or DEFAULT_PREFS.copy()

    score = 0
    breakdown = []

    if cv_data:
        score += 30
        breakdown.append({"label": "CV uploaded", "points": 30, "done": True})
    else:
        breakdown.append({"label": "Upload your CV", "points": 30, "done": False})

    skills = (cv_data or {}).get("skills", [])
    if len(skills) >= 5:
        score += 20
        breakdown.append({"label": f"{len(skills)} skills detected", "points": 20, "done": True})
    else:
        breakdown.append({"label": "CV needs more skills (aim for 5+)", "points": 20, "done": False})

    has_prefs = bool(preferences.get("job_titles") and preferences.get("locations"))
    if has_prefs:
        score += 20
        breakdown.append({"label": "Preferences configured", "points": 20, "done": True})
    else:
        breakdown.append({"label": "Set job titles & locations in Preferences", "points": 20, "done": False})

    has_salary = bool(preferences.get("salary_min") or preferences.get("salary_expectation"))
    if has_salary:
        score += 15
        breakdown.append({"label": "Salary expectation set", "points": 15, "done": True})
    else:
        breakdown.append({"label": "Add salary expectation in Preferences", "points": 15, "done": False})

    has_gmail = bool(preferences.get("gmail_address") and preferences.get("gmail_app_password"))
    if has_gmail:
        score += 15
        breakdown.append({"label": "Email alerts configured", "points": 15, "done": True})
    else:
        breakdown.append({"label": "Configure Gmail in Preferences for email alerts", "points": 15, "done": False})

    return jsonify({"ok": True, "score": score, "breakdown": breakdown})


# ── Outreach agent routes ────────────────────────────────────────────────────

@app.route("/outbox")
def outbox():
    """
    Outbox page showing outreach drafts (pending/sent/skipped).
    Paginated server-side — the queue has ~1k+ rows and rendering all of
    them shipped 10+ MB of HTML per request.
    """
    from database import get_outreach_queue, get_outreach_queue_count
    PER_PAGE = 50
    tab = (request.args.get("tab") or "pending").lower()
    if tab not in ("pending", "sent", "skipped"):
        tab = "pending"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    totals = {s: get_outreach_queue_count(s) for s in ("pending", "sent", "skipped")}
    total_pages = max(1, (totals[tab] + PER_PAGE - 1) // PER_PAGE)
    page = min(page, total_pages)
    page_rows = get_outreach_queue(tab, limit=PER_PAGE, offset=(page - 1) * PER_PAGE)

    return render_template(
        "outbox.html",
        tab=tab,
        rows=page_rows,
        totals=totals,
        page=page,
        total_pages=total_pages,
        per_page=PER_PAGE,
    )


@app.route("/api/approve/<token>")
@csrf.exempt
def approve_outreach(token):
    """Approve and send a cold email for the given token."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from database import claim_outreach_token

    item = claim_outreach_token(token)
    if item is None:
        return "This approval link has already been used, has expired, or is invalid.", 200

    prefs = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())
    gmail_address = prefs.get("gmail_address", "")
    gmail_password = prefs.get("gmail_app_password", "")

    if not gmail_address or not gmail_password:
        return "Gmail not configured. Please set it up in Preferences.", 500

    recipient_email = item.get("hm_email", "")
    if not recipient_email:
        return "No hiring manager email on file for this job.", 400

    try:
        apply_url = (item.get("apply_url") or "").strip()
        email_body = item["email_draft"]
        if apply_url:
            email_body += f"\n\nJob posting: {apply_url}"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Regarding the {item['role']} role at {item['company']}"
        msg["From"] = gmail_address
        msg["To"] = recipient_email
        msg.attach(MIMEText(email_body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_address, gmail_password)
            smtp.sendmail(gmail_address, recipient_email, msg.as_string())

        # claim_outreach_token already set status='sent'; mark job applied too
        _uid = current_user_id()
        if _uid:
            update_applied_status_user(_uid, item["job_id"], 1)
        else:
            update_applied_status(item["job_id"], 1)
        return render_template("approve_result.html",
                               success=True, item=item,
                               message=f"Email sent to {recipient_email}!")
    except Exception as e:
        logger.error("approve_outreach send failed: %s", e)
        return f"Failed to send email: {e}", 500


@app.route("/api/skip/<token>")
@csrf.exempt
def skip_outreach(token):
    """Mark an outreach draft as skipped."""
    from database import get_outreach_by_token, update_outreach_status

    item = get_outreach_by_token(token)
    if not item:
        return "Invalid or expired link.", 404
    update_outreach_status(token, "skipped")
    return render_template("approve_result.html",
                           success=False, item=item,
                           message="Skipped. This job won't appear again.")


@app.route("/api/outreach/map")
def outreach_map():
    """Return {job_id: outreach_data} for all outreach queue entries. Used by jobs page."""
    from database import get_outreach_map
    return jsonify(get_outreach_map())


@app.route("/api/outreach/<token>/save", methods=["POST"])
def save_outreach_draft(token):
    """Save edited draft text for an outreach item."""
    from database import update_outreach_draft, get_outreach_by_token
    item = get_outreach_by_token(token)
    if not item:
        return jsonify({"ok": False, "error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    email_draft = data.get("email_draft")
    linkedin_draft = data.get("linkedin_draft")
    update_outreach_draft(token, email_draft=email_draft, linkedin_draft=linkedin_draft)
    return jsonify({"ok": True})


@app.route("/api/outreach/<token>/mark-applied", methods=["POST"])
def mark_outreach_applied(token):
    """Mark the job associated with this outreach token as Applied."""
    from database import get_outreach_by_token
    item = get_outreach_by_token(token)
    if not item:
        return jsonify({"ok": False, "error": "Not found"}), 404
    _uid = current_user_id()
    if _uid:
        update_applied_status_user(_uid, item["job_id"], 1)
    else:
        update_applied_status(item["job_id"], 1)
    return jsonify({"ok": True})


def _run_agent_background():
    """Run the AI agent pipeline in a background thread."""
    global agent_status
    try:
        import json as _json
        from agent.graph import run_agent_pipeline
        prefs = load_preferences() or DEFAULT_PREFS.copy()
        with open(os.path.join(BASE_DIR, "config.json")) as f:
            _config = _json.load(f)
        result = run_agent_pipeline(prefs, _config)
        with agent_lock:
            agent_status["queued"] = result.get("queued_count", 0)
            agent_status["error"] = result.get("errors", [None])[0] if result.get("errors") else None
    except Exception as e:
        logger.error("Agent background run error: %s", e)
        with agent_lock:
            agent_status["error"] = str(e)
    finally:
        with agent_lock:
            agent_status["running"] = False
            agent_status["finished_at"] = datetime.now().isoformat()


@app.route("/api/agent/run", methods=["POST"])
def run_agent_now():
    """Manually trigger the AI agent pipeline."""
    require_user_id()
    global agent_status
    with agent_lock:
        if agent_status["running"]:
            return jsonify({"ok": False, "error": "Agent is already running"}), 409
        agent_status = {"running": True, "queued": 0, "error": None, "finished_at": None}
    t = threading.Thread(target=_run_agent_background, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/agent/status")
def agent_run_status():
    """Return current agent run state."""
    with agent_lock:
        return jsonify(dict(agent_status))


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
    user_email = dict(session).get('user', {}).get('email')
    if user_email:
        all_reminders = [r for r in all_reminders if not r.get('owner_email') or r.get('owner_email') == user_email]
    return render_template("reminders.html", reminders=all_reminders)


@app.route("/reminders/create", methods=["POST"])
def reminders_create():
    """Create a new reminder. Supports type='jobs' (default) or type='prd'."""
    from reminder_runner import load_reminders, save_reminders
    rem_type = (request.form.get("type") or "jobs").strip().lower()
    if rem_type not in ("jobs", "prd"):
        rem_type = "jobs"

    name = request.form.get("name", "").strip()
    email_addr = request.form.get("email", "").strip()
    if not name or not email_addr:
        flash("Name and email are required.", "error")
        return redirect(url_for("reminders"))
    if "@" not in email_addr or "." not in email_addr.split("@")[-1]:
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("reminders"))

    if rem_type == "prd":
        try:
            prd_hour = max(0, min(23, int(request.form.get("prd_hour", 8))))
        except (ValueError, TypeError):
            prd_hour = 8
        prd_domain = (request.form.get("prd_domain") or "").strip()
        user_email = dict(session).get('user', {}).get('email')
        all_reminders = load_reminders()
        all_reminders.append({
            "id": uuid.uuid4().hex,
            "owner_email": user_email,
            "type": "prd",
            "name": name,
            "email": email_addr,
            "enabled": True,
            "last_sent": None,
            "prd_hour": prd_hour,
            "prd_domain": prd_domain,
        })
        try:
            save_reminders(all_reminders)
        except OSError as e:
            flash(f"Failed to save reminder: {e}", "error")
            return redirect(url_for("reminders"))
        flash(f"PRD reminder '{name}' created — daily PRD will be emailed at {prd_hour:02d}:00.", "success")
        return redirect(url_for("reminders"))

    # type == 'jobs' (legacy path)
    keyword = request.form.get("keyword", "").strip()
    if not keyword:
        flash("Keyword is required for job alerts.", "error")
        return redirect(url_for("reminders"))
    try:
        min_score = max(0, min(100, int(request.form.get("min_score", 65))))
        max_jobs = max(1, min(50, int(request.form.get("max_jobs", 20))))
    except (ValueError, TypeError):
        flash("Score and max jobs must be numbers.", "error")
        return redirect(url_for("reminders"))

    cv_file = request.files.get("cv_file")
    if not cv_file or not cv_file.filename:
        flash("A CV/resume file is required to create a job-alert reminder.", "error")
        return redirect(url_for("reminders"))
    try:
        cv_text = _extract_cv_text(cv_file)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("reminders"))
    cv_data = parse_cv_text(cv_text)
    user_email = dict(session).get('user', {}).get('email')

    all_reminders = load_reminders()
    all_reminders.append({
        "id": uuid.uuid4().hex,
        "owner_email": user_email,
        "type": "jobs",
        "name": name,
        "keyword": keyword,
        "min_score": min_score,
        "max_jobs": max_jobs,
        "email": email_addr,
        "enabled": True,
        "last_sent": None,
        "cv_data": cv_data,
        "hr_email_enabled": False,
        "hr_email_hour": 11,
        "hr_email_minute": 0,
        "hr_location": "",
        "last_hr_sent": None,
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
    try:
        save_reminders(all_reminders)
    except OSError as e:
        flash(f"Failed to save: {e}", "error")
        return redirect(url_for("reminders"))
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
    try:
        save_reminders(all_reminders)
    except OSError as e:
        flash(f"Failed to save: {e}", "error")
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

    recipient = (reminder.get("email") or "").strip()
    name = reminder.get("name", "Reminder")

    # PRD branch: generate today's PRD and email it via SMTP.
    if (reminder.get("type") or "jobs").lower() == "prd":
        try:
            from prd_generator import generate_daily_prd, build_prd_email_html
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            prd = generate_daily_prd()
            html_body = build_prd_email_html(prd)
            subject = f"📋 Daily PRD: {prd['product']['name']} ({prd['date']})"

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = gmail_address
            msg["To"] = recipient
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(gmail_address, gmail_app_password)
                server.sendmail(gmail_address, [recipient], msg.as_string())

            reminder["last_sent"] = datetime.now().isoformat()
            save_reminders(all_reminders)
            flash(f"Sent PRD '{prd['product']['name']}' to {recipient}.", "success")
        except Exception as e:
            logger.exception("PRD reminder send failed: %s", e)
            flash(f"PRD send failed: {e}", "error")
        return redirect(url_for("reminders"))

    keyword = (reminder.get("keyword") or "").strip()
    min_score = max(0, min(100, int(reminder.get("min_score", 65))))
    max_jobs = max(1, min(50, int(reminder.get("max_jobs", 20))))

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


@app.route("/reminders/<reminder_id>/hr-toggle", methods=["POST"])
def reminders_hr_toggle(reminder_id):
    """Enable or disable HR email for a reminder."""
    from reminder_runner import load_reminders, save_reminders
    all_reminders = load_reminders()
    for r in all_reminders:
        if r.get("id") == reminder_id:
            r["hr_email_enabled"] = not r.get("hr_email_enabled", False)
            break
    save_reminders(all_reminders)
    _reschedule_hr_jobs()
    return redirect(url_for("reminders"))


@app.route("/reminders/<reminder_id>/hr-schedule", methods=["POST"])
def reminders_hr_schedule(reminder_id):
    """Update HR email send time for a reminder."""
    from reminder_runner import load_reminders, save_reminders
    try:
        hour   = max(0, min(23, int(request.form.get("hr_hour", 11))))
        minute = max(0, min(59, int(request.form.get("hr_minute", 0))))
        location = request.form.get("hr_location", "").strip()
    except (ValueError, TypeError):
        flash("Invalid time values.", "error")
        return redirect(url_for("reminders"))
    all_reminders = load_reminders()
    for r in all_reminders:
        if r.get("id") == reminder_id:
            r["hr_email_hour"]   = hour
            r["hr_email_minute"] = minute
            if location:
                r["hr_location"] = location
            break
    save_reminders(all_reminders)
    _reschedule_hr_jobs()
    flash("HR email schedule updated.", "success")
    return redirect(url_for("reminders"))


@app.route("/reminders/<reminder_id>/hr-send", methods=["POST"])
def reminders_hr_send(reminder_id):
    """Send HR email right now for a reminder."""
    import threading
    threading.Thread(target=_run_hr_email, args=[reminder_id], daemon=True).start()
    flash("Sending HR email in background — check your inbox in ~1 minute.", "success")
    return redirect(url_for("reminders"))


@app.route("/reminders/<reminder_id>/edit", methods=["POST"])
def reminders_edit(reminder_id):
    """Update editable fields of an existing reminder."""
    from reminder_runner import load_reminders, save_reminders
    name = request.form.get("name", "").strip()
    keyword = request.form.get("keyword", "").strip()
    email_addr = request.form.get("email", "").strip()
    if not name or not keyword or not email_addr:
        flash("Name, keyword, and email are required.", "error")
        return redirect(url_for("reminders"))
    if "@" not in email_addr or "." not in email_addr.split("@")[-1]:
        flash("Please enter a valid email address.", "error")
        return redirect(url_for("reminders"))
    try:
        min_score = max(0, min(100, int(request.form.get("min_score", 65))))
        max_jobs = max(1, min(50, int(request.form.get("max_jobs", 20))))
    except (ValueError, TypeError):
        flash("Score and max jobs must be numbers.", "error")
        return redirect(url_for("reminders"))

    all_reminders = load_reminders()
    updated = False
    for r in all_reminders:
        if r.get("id") == reminder_id:
            r["name"] = name
            r["keyword"] = keyword
            r["email"] = email_addr
            r["min_score"] = min_score
            r["max_jobs"] = max_jobs
            updated = True
            break
    if updated:
        try:
            save_reminders(all_reminders)
        except OSError as e:
            flash(f"Failed to save reminder: {e}", "error")
            return redirect(url_for("reminders"))
        flash(f"Reminder '{name}' updated.", "success")
    else:
        flash("Reminder not found.", "error")
    return redirect(url_for("reminders"))



@app.route("/api/scheduler/jobs")
def scheduler_jobs():
    """List all scheduled jobs and their next run times."""
    if not _scheduler:
        return jsonify({"ok": False, "error": "Scheduler not running"})
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })
    return jsonify({"ok": True, "jobs": jobs})



# ---------------------------------------------------------------------------
# How it Works
# ---------------------------------------------------------------------------

@app.route("/how-it-works")
def how_it_works():
    return render_template("how_it_works.html")


# ---------------------------------------------------------------------------
# Setup / Profile (career-ops integration)
# ---------------------------------------------------------------------------

CAREER_OPS_DIR  = os.path.join(BASE_DIR, "tmp_career_ops")
PROFILE_YML     = os.path.join(CAREER_OPS_DIR, "config", "profile.yml")
CV_MD           = os.path.join(CAREER_OPS_DIR, "cv.md")


def _load_profile() -> dict:
    """Load profile.yml as a dict with safe nested defaults."""
    import yaml
    if not os.path.exists(PROFILE_YML):
        return {}
    with open(PROFILE_YML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_profile(data: dict) -> None:
    import yaml
    os.makedirs(os.path.dirname(PROFILE_YML), exist_ok=True)
    with open(PROFILE_YML, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _nested(d: dict, *keys, default=None):
    """Safe nested dict access."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
    return d if d is not None else default


@app.route("/setup", methods=["GET", "POST"])
def setup_profile():
    import yaml

    profile = _load_profile()

    # Ensure nested dicts exist for template rendering
    profile.setdefault("candidate", {})
    profile.setdefault("narrative", {})
    profile.setdefault("target_roles", {})
    profile["target_roles"].setdefault("locations", {})
    profile.setdefault("compensation", {})

    cv_content = ""
    if os.path.exists(CV_MD):
        with open(CV_MD, "r", encoding="utf-8") as f:
            cv_content = f.read()

    # Detect completeness for the status dots
    todo_markers = ["TODO", ""]
    profile_complete = all([
        profile["candidate"].get("full_name", "TODO") not in todo_markers,
        profile["candidate"].get("email", "TODO") not in todo_markers,
        _nested(profile, "narrative", "exit_story", default="TODO") not in todo_markers,
        _nested(profile, "compensation", "target_range", default="TODO") not in todo_markers,
    ])
    cv_complete = bool(cv_content) and "TODO" not in cv_content

    if request.method == "POST":
        tab = request.form.get("tab", "profile")

        if tab == "profile":
            profile["candidate"]["full_name"]    = request.form.get("full_name", "").strip()
            profile["candidate"]["email"]        = request.form.get("email", "").strip()
            profile["candidate"]["phone"]        = request.form.get("phone", "").strip()
            profile["candidate"]["location"]     = request.form.get("location", "").strip()
            profile["candidate"]["linkedin"]     = request.form.get("linkedin", "").strip()
            profile["candidate"]["portfolio_url"]= request.form.get("portfolio_url", "").strip()

            profile["narrative"]["headline"]     = request.form.get("headline", "").strip()
            profile["narrative"]["exit_story"]   = request.form.get("exit_story", "").strip()
            superpowers_raw = request.form.get("superpowers", "")
            profile["narrative"]["superpowers"]  = [s.strip() for s in superpowers_raw.split(",") if s.strip()]

            # Proof points (parallel arrays from form)
            names   = request.form.getlist("proof_name[]")
            urls    = request.form.getlist("proof_url[]")
            metrics = request.form.getlist("proof_metric[]")
            proof_points = []
            for n, u, m in zip(names, urls, metrics):
                if n.strip():
                    proof_points.append({"name": n.strip(), "url": u.strip(), "hero_metric": m.strip()})
            profile["narrative"]["proof_points"] = proof_points

            _save_profile(profile)
            flash("Profile saved successfully.", "success")
            return redirect(url_for("setup_profile") + "?tab=profile")

        elif tab == "targets":
            primary_raw = request.form.get("primary_roles", "")
            profile["target_roles"]["primary"] = [r.strip() for r in primary_raw.splitlines() if r.strip()]

            locs_raw = request.form.get("locations", "")
            profile["target_roles"]["locations"]["preferred"] = [l.strip() for l in locs_raw.splitlines() if l.strip()]

            ind_raw = request.form.get("industries", "")
            profile["target_roles"]["industries"] = [i.strip() for i in ind_raw.splitlines() if i.strip()]

            db_raw = request.form.get("deal_breakers", "")
            profile["deal_breakers"] = [d.strip() for d in db_raw.splitlines() if d.strip()]

            _save_profile(profile)
            flash("Target roles saved.", "success")
            return redirect(url_for("setup_profile") + "?tab=targets")

        elif tab == "comp":
            profile["compensation"]["target_range"]         = request.form.get("target_range", "").strip()
            profile["compensation"]["currency"]             = request.form.get("currency", "INR").strip()
            profile["compensation"]["minimum"]              = request.form.get("minimum", "").strip()
            profile["compensation"]["location_flexibility"] = request.form.get("location_flexibility", "").strip()

            _save_profile(profile)
            flash("Compensation details saved.", "success")
            return redirect(url_for("setup_profile") + "?tab=comp")

        elif tab == "cv":
            cv_text = request.form.get("cv_content", "")
            os.makedirs(os.path.dirname(CV_MD), exist_ok=True)
            with open(CV_MD, "w", encoding="utf-8") as f:
                f.write(cv_text)
            flash("CV saved successfully.", "success")
            return redirect(url_for("setup_profile") + "?tab=cv")

        return redirect(url_for("setup_profile"))

    return render_template(
        "setup.html",
        profile=profile,
        cv_content=cv_content,
        profile_complete=profile_complete,
        cv_complete=cv_complete,
    )


# ---------------------------------------------------------------------------
# PRD Library
# ---------------------------------------------------------------------------

@app.route("/prds")
def prd_library():
    from prd_generator import list_prds, generate_daily_prd
    prds = list_prds()
    today_prd = None
    try:
        today_prd = generate_daily_prd()
    except Exception:
        pass
    return render_template("prd_library.html", prds=prds, today_prd=today_prd, detail=None)


@app.route("/prds/<date_str>")
def prd_detail(date_str):
    import re
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        from flask import abort
        abort(400)
    from prd_generator import PRD_DIR
    import json
    cache_path = os.path.join(PRD_DIR, f"prd_{date_str}.json")
    if not os.path.exists(cache_path):
        flash("PRD not found.", "error")
        return redirect(url_for("prd_library"))
    with open(cache_path) as f:
        prd = json.load(f)
    return render_template("prd_library.html", prds=[], today_prd=prd, detail=prd)


@app.route("/api/prd/send-now", methods=["POST"])
def prd_send_now():
    """Manually trigger today's PRD email."""
    require_user_id()
    try:
        threading.Thread(target=_send_prd_email_job, daemon=True).start()
        return jsonify({"ok": True, "message": "PRD email queued"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/prd/<date_str>")
def prd_json(date_str):
    import re
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        from flask import abort
        abort(400)
    from prd_generator import PRD_DIR
    import json
    cache_path = os.path.join(PRD_DIR, f"prd_{date_str}.json")
    if not os.path.exists(cache_path):
        return jsonify({"error": "not found"}), 404
    with open(cache_path) as f:
        return jsonify(json.load(f))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Use FLASK_DEBUG env var so LaunchAgent can force production mode.
    # Default to debug=True only when not set (i.e. manual terminal run).
    import os as _os
    _debug = _os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(debug=_debug, port=int(os.environ.get("PORT", 5002)))
