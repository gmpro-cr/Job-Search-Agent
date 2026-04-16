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
from database import update_job_description
from telegram_notifier import send_telegram_alert, send_telegram_batch_summary
from telegram_bot import start_telegram_bot
from git_sync import sync_from_scrape

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
_scraper_stop_event = threading.Event()   # set() to request stop

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
    """Load hiring_managers module from Documents/Claude via importlib."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("hiring_managers",
                                         os.path.join(_HR_EMAIL_DIR, "hiring_managers.py"))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
                open(prd_sent_flag, "w").close()
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

    _ran_today: dict = {}   # job_key -> date string of last run

    def _already_ran(key: str, today: str) -> bool:
        return _ran_today.get(key) == today

    def _mark_ran(key: str, today: str):
        _ran_today[key] = today

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
                            open(flag, "w").close()
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
    global scraper_status

    def _stopped():
        return _scraper_stop_event.is_set()

    def _mark_stopped():
        with scraper_lock:
            scraper_status["phase"] = "stopped"
            scraper_status["finished_at"] = datetime.now().isoformat()
            scraper_status["running"] = False
        logger.info("Scraper stopped by user request")

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

@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/favicon.ico")
def favicon():
    # Return 204 No Content to avoid 500 errors when favicon is missing
    return "", 204


@app.route("/dashboard")
def dashboard():
    from database import get_dashboard_insights
    stats = get_comprehensive_stats()
    portal_quality = get_portal_quality_stats()
    pipeline = get_application_pipeline_stats()
    categories = get_best_matching_categories()
    activity = get_application_activity()
    recommendations = get_recommended_actions()
    insights = get_dashboard_insights()
    return render_template(
        "dashboard.html", stats=stats, portal_quality=portal_quality,
        pipeline=pipeline, categories=categories, activity=activity,
        recommendations=recommendations, insights=insights,
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
    company_stage = filters.get("company_stage", "")

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

    if company_stage:
        conditions.append("company_funding_stage = ?")
        params.append(company_stage)

    sort_map = {
        "score_desc": "relevance_score DESC",
        "score_asc": "relevance_score ASC",
        "date_desc": "date_found DESC",
        "date_asc": "date_found ASC",
        "company_asc": "company ASC",
        "cv_score_desc": "cv_score DESC",
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
        "company_stage": request.args.get("company_stage", ""),
    }
    conditions, params, order = _build_jobs_query(filters)
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
    cursor.execute(f"SELECT COUNT(*) as count FROM job_listings{where}", params)
    total = cursor.fetchone()["count"]

    # Fetch paginated matching jobs
    cursor.execute(
        f"SELECT * FROM job_listings{where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Attach inline gap data if CV is uploaded
    cv_data = load_cv_data()
    cv_uploaded = cv_data is not None
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

    # Get normalized locations for filter dropdown (canonical name + count)
    normalized_locs = get_normalized_locations()

    clean_filters = {k: v for k, v in filters.items() if v and v != "0"}

    return render_template(
        "jobs.html",
        jobs=rows, total=total,
        portals=portals, locations=normalized_locs,
        filters=filters, clean_filters=clean_filters,
        cv_uploaded=cv_uploaded,
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
            "linkedin_email": request.form.get("linkedin_email", "").strip(),
            "linkedin_password": request.form.get("linkedin_password", "").strip(),
            "agent_score_threshold": max(0, min(100, int(request.form.get("agent_score_threshold", "50")))),
            "agent_job_cap": max(10, min(500, int(request.form.get("agent_job_cap", "200")))),
            "agent_host": request.form.get("agent_host", "http://localhost:5001").strip(),
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
        "linkedin_password": bool(os.environ.get("LINKEDIN_PASSWORD")),
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
    return render_template("scraper.html", config=load_config())


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


@app.route("/api/portals/update", methods=["POST"])
def update_portals():
    """Enable or disable job portals. Persists to config.json."""
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
    t = threading.Thread(target=_run_scraper_pipeline, daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/scraper/stop", methods=["POST"])
def stop_scraper():
    global scraper_status
    with scraper_lock:
        if not scraper_status["running"]:
            return jsonify({"ok": False, "error": "Scraper is not running"}), 409
    _scraper_stop_event.set()
    with scraper_lock:
        scraper_status["phase"] = "stopping"
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


@app.route("/api/cv/skills-gap")
def cv_skills_gap():
    """Return skill frequency across target-role jobs vs CV skills."""
    cv_data = load_cv_data()
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
    """Outbox page showing all outreach drafts (pending/sent/skipped)."""
    from database import get_outreach_queue
    pending = get_outreach_queue("pending")
    sent = get_outreach_queue("sent")
    skipped = get_outreach_queue("skipped")
    return render_template("outbox.html",
                           pending=pending, sent=sent, skipped=skipped)


@app.route("/api/approve/<token>")
def approve_outreach(token):
    """Approve and send a cold email for the given token."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from database import get_outreach_by_token, update_outreach_status

    item = get_outreach_by_token(token)
    if not item:
        return "Invalid or expired link.", 404
    if item["status"] != "pending":
        return f"This outreach was already {item['status']}.", 200

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

        update_outreach_status(token, "sent")
        # Also mark the job as Applied in job_listings
        update_applied_status(item["job_id"], 1)
        return render_template("approve_result.html",
                               success=True, item=item,
                               message=f"Email sent to {recipient_email}!")
    except Exception as e:
        logger.error("approve_outreach send failed: %s", e)
        return f"Failed to send email: {e}", 500


@app.route("/api/skip/<token>")
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
    if "@" not in email_addr or "." not in email_addr.split("@")[-1]:
        flash("Please enter a valid email address.", "error")
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


# ── Autoresearch routes ──────────────────────────────────────────────────────

@app.route("/autoresearch")
def autoresearch_page():
    """Autoresearch dashboard page."""
    from autoresearch.loop import get_status
    import json, os
    results_path = os.path.join(BASE_DIR, "autoresearch", "results.json")
    results = []
    if os.path.exists(results_path):
        with open(results_path) as f:
            results = json.load(f)
    baseline_path = os.path.join(BASE_DIR, "autoresearch", "baseline.json")
    baseline = None
    if os.path.exists(baseline_path):
        with open(baseline_path) as f:
            baseline = json.load(f)
    prompt_path = os.path.join(BASE_DIR, "autoresearch", "scoring_prompt.md")
    current_prompt = ""
    if os.path.exists(prompt_path):
        with open(prompt_path) as f:
            current_prompt = f.read()
    return render_template(
        "autoresearch.html",
        status=get_status(),
        results=list(reversed(results[-50:])),
        baseline=baseline,
        current_prompt=current_prompt,
    )


@app.route("/api/autoresearch/start", methods=["POST"])
def autoresearch_start():
    """Start the autoresearch loop in a background thread."""
    import threading
    from autoresearch.loop import run_loop, get_status
    status = get_status()
    if status["running"]:
        return jsonify({"ok": False, "error": "Already running"}), 400
    data = request.get_json(silent=True) or {}
    max_exp = int(data.get("max_experiments", 10))
    t = threading.Thread(target=run_loop, kwargs={"max_experiments": max_exp}, daemon=True)
    t.start()
    return jsonify({"ok": True, "max_experiments": max_exp})


@app.route("/api/autoresearch/stop", methods=["POST"])
def autoresearch_stop():
    """Stop the running loop."""
    from autoresearch.loop import stop
    stop()
    return jsonify({"ok": True})


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


@app.route("/api/autoresearch/status")
def autoresearch_status():
    """Poll current loop status (used by UI)."""
    from autoresearch.loop import get_status
    return jsonify(get_status())


@app.route("/api/autoresearch/seed", methods=["POST"])
def autoresearch_seed():
    """Run seed.py to create testset.json (one-time setup)."""
    import threading, os
    testset_path = os.path.join(BASE_DIR, "autoresearch", "testset.json")
    force = (request.get_json(silent=True) or {}).get("force", False)
    if os.path.exists(testset_path) and not force:
        return jsonify({"ok": False, "error": "testset.json already exists. Pass force=true to reseed."})
    def _do_seed():
        from autoresearch.seed import seed
        seed(n=30, force=force)
    threading.Thread(target=_do_seed, daemon=True).start()
    return jsonify({"ok": True, "message": "Seeding started in background (takes ~2 min)"})




# ---------------------------------------------------------------------------
# Agent Pipeline
# ---------------------------------------------------------------------------

def _parse_pipeline_md(path: str) -> list[dict]:
    """Parse pipeline.md into a list of job dicts."""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    jobs = []
    # Split on --- separators (after the header comment block)
    marker = "<!-- Scraper appends entries below this line -->"
    if marker in content:
        content = content.split(marker, 1)[1]

    blocks = [b.strip() for b in content.split("---") if b.strip()]
    for block in blocks:
        lines = block.splitlines()
        job = {"company": "", "role": "", "score": 0, "portal": "", "date": "",
               "url": "", "location": "", "salary": "", "skills": [], "description": ""}
        for line in lines:
            line = line.strip()
            if line.startswith("## "):
                parts = line[3:].split(" — ", 1)
                job["company"] = parts[0].strip()
                job["role"] = parts[1].strip() if len(parts) > 1 else ""
            elif line.startswith("**Score:**"):
                import re
                m = re.search(r'\*\*Score:\*\*\s*(\d+)', line)
                if m:
                    job["score"] = int(m.group(1))
                pm = re.search(r'\*\*Portal:\*\*\s*(\S+)', line)
                if pm:
                    job["portal"] = pm.group(1)
                dm = re.search(r'\*\*Date:\*\*\s*(\S+)', line)
                if dm:
                    job["date"] = dm.group(1)
            elif line.startswith("**URL:**"):
                job["url"] = line.replace("**URL:**", "").strip()
            elif line.startswith("**Location:**"):
                loc_salary = line.replace("**Location:**", "").split("**Salary:**")
                job["location"] = loc_salary[0].strip()
                if len(loc_salary) > 1:
                    job["salary"] = loc_salary[1].strip()
            elif line.startswith("**Skills:**"):
                raw = line.replace("**Skills:**", "").strip()
                job["skills"] = [s.strip() for s in raw.split(",") if s.strip()]
            elif line.startswith("**Description:**"):
                pass  # description follows on next lines
        if job["company"]:
            jobs.append(job)
    return jobs


def _parse_applications_md(path: str) -> list[dict]:
    """Parse applications.md tracker table into list of dicts."""
    if not os.path.exists(path):
        return []
    apps = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|") or line.startswith("| #") or line.startswith("|--") or line.startswith("| ---"):
                continue
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 6:
                apps.append({
                    "num": cols[0], "date": cols[1], "company": cols[2],
                    "role": cols[3], "score": cols[4], "status": cols[5],
                    "notes": cols[7] if len(cols) > 7 else "",
                })
    return apps


@app.route("/pipeline")
def pipeline():
    import os
    career_ops = os.path.join(BASE_DIR, "tmp_career_ops")
    pipeline_path = os.path.join(career_ops, "data", "pipeline.md")
    apps_path = os.path.join(career_ops, "data", "applications.md")
    cv_path = os.path.join(career_ops, "cv.md")
    profile_path = os.path.join(career_ops, "config", "profile.yml")

    queued_jobs = _parse_pipeline_md(pipeline_path)

    # Check if CV has actual content (not just template)
    cv_ready = False
    if os.path.exists(cv_path):
        cv_text = open(cv_path).read()
        cv_ready = bool(cv_text.strip()) and "TODO" not in cv_text

    # Check profile completeness
    profile_ready = False
    if os.path.exists(profile_path):
        import yaml
        try:
            p = yaml.safe_load(open(profile_path)) or {}
            name = (p.get("candidate") or {}).get("full_name", "")
            email = (p.get("candidate") or {}).get("email", "")
            profile_ready = bool(name and "TODO" not in name and email)
        except Exception:
            pass

    applications = _parse_applications_md(apps_path)

    return render_template(
        "pipeline.html",
        queued_jobs=queued_jobs,
        queue_count=len(queued_jobs),
        applied_count=len(applications),
        applications=applications,
        cv_ready=cv_ready,
        profile_ready=profile_ready,
        career_ops_path=career_ops,
    )


@app.route("/api/pipeline/open-terminal", methods=["POST"])
def pipeline_open_terminal():
    """Open Terminal.app at the career-ops directory (macOS)."""
    import subprocess
    career_ops = os.path.join(BASE_DIR, "tmp_career_ops")
    try:
        # macOS: open a new Terminal window cd'd into career_ops
        script = f'tell application "Terminal" to do script "cd {career_ops} && clear && echo \\"Run: claude\\" && echo \\"Then type: /career-ops pipeline\\""'
        subprocess.Popen(["osascript", "-e", script])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


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
    try:
        threading.Thread(target=_send_prd_email_job, daemon=True).start()
        return jsonify({"ok": True, "message": "PRD email queued"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/prd/<date_str>")
def prd_json(date_str):
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
    app.run(debug=_debug, port=5001)
