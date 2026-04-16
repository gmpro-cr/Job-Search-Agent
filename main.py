#!/usr/bin/env python3
"""
main.py - Job Search Agent: Automated multi-portal job scraper with
intelligent filtering, scoring, and daily digest generation.

Usage:
    python main.py                    Run agent once and generate digest
    python main.py --schedule         Setup daily scheduling
    python main.py --edit-preferences Modify user preferences
    python main.py --view-stats       Show job search statistics
    python main.py --manual-run       Run immediately (same as no args)
    python main.py --last-digest      Open the most recent digest
    python main.py --portal-stats     Show portal quality statistics
"""

import argparse
import json
import logging
import os
import sys
import webbrowser
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

# Load .env before anything else reads os.environ
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

# Project modules
from database import (
    init_db,
    insert_jobs_bulk,
    mark_sent_in_digest,
    generate_job_id,
    get_comprehensive_stats,
    get_portal_quality_stats,
    get_unsent_jobs,
)
from scrapers import scrape_all_portals
from analyzer import analyze_jobs
from digest_generator import generate_digest, get_latest_digest
from scheduler import setup_scheduler
from queue_exporter import export_to_pipeline

# =============================================================================
# Paths
# =============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_IS_VERCEL = bool(os.environ.get("VERCEL"))
if _IS_VERCEL:
    DATA_DIR = "/tmp"
else:
    DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
PREFS_PATH = os.path.join(DATA_DIR, "user_preferences.json")
LOG_PATH = os.path.join(DATA_DIR, "job_agent.log")


# =============================================================================
# Logging setup
# =============================================================================

def setup_logging(config):
    log_level = getattr(logging, config.get("logging", {}).get("log_level", "INFO"))
    max_days = config.get("logging", {}).get("max_log_days", 30)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # File handler with daily rotation
    file_handler = TimedRotatingFileHandler(
        LOG_PATH, when="midnight", interval=1, backupCount=max_days, encoding="utf-8"
    )
    file_handler.setLevel(log_level)
    file_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler.setFormatter(file_fmt)

    # Console handler (less verbose)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_fmt = logging.Formatter("[%(levelname)s] %(message)s")
    console_handler.setFormatter(console_fmt)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    # Return defaults if no config file
    return {
        "portals": {},
        "scraping": {"thread_count": 4, "request_delay_min": 2, "request_delay_max": 5, "max_retries": 3, "portal_timeout": 30, "cache_expiry_hours": 12},
        "scoring": {"min_relevance_score": 65, "ollama_model": "mistral", "ollama_timeout": 60, "use_ollama": True},
        "digest": {"open_in_browser": True, "keep_days": 90},
        "logging": {"log_file": "job_agent.log", "max_log_days": 30, "log_level": "INFO"},
    }


# =============================================================================
# User Preferences (Interactive Setup)
# =============================================================================

DEFAULT_PREFS = {
    "job_titles": ["Product Manager", "PM", "Associate PM", "Product Lead"],
    "locations": ["Remote", "Bangalore", "Delhi", "Mumbai", "Pune"],
    "industries": ["Fintech", "SaaS", "AI/ML", "Banking", "E-commerce", "Crypto"],
    "transferable_skills": [
        "Stakeholder Management", "Risk Management", "Regulatory Compliance",
        "Data Analysis", "P&L Ownership", "Process Optimization",
        "Cross-functional Leadership", "Client Relationship Management",
    ],
    "top_jobs_per_digest": 5,
    "digest_time": "6:00 AM",
    "email": "",
    "gmail_address": "",
    "gmail_app_password": "",
    "apollo_api_key": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_min_score": 65,
    "agent_score_threshold": 50,   # min LLM score to include a job in outreach
    "agent_job_cap": 200,          # max jobs fetched per agent run
    "agent_host": "http://localhost:5001",  # base URL for approve/skip links in email
    "linkedin_email": "",
    "linkedin_password": "",
}

# Keys that should be stored in .env, not in user_preferences.json
_CREDENTIAL_KEYS = {"gmail_app_password", "telegram_bot_token", "apollo_api_key", "linkedin_password"}


def load_preferences():
    if os.path.exists(PREFS_PATH):
        with open(PREFS_PATH, "r") as f:
            return json.load(f)
    return None


def save_preferences(prefs):
    # Strip credential keys when corresponding env vars are set so secrets
    # stay only in .env and never land in the JSON file.
    clean = dict(prefs)
    env_cred_map = {
        "gmail_app_password": "GMAIL_APP_PASSWORD",
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
        "apollo_api_key": "APOLLO_API_KEY",
        "linkedin_password": "LINKEDIN_PASSWORD",
    }
    for pref_key, env_key in env_cred_map.items():
        if os.environ.get(env_key):
            clean.pop(pref_key, None)
    with open(PREFS_PATH, "w") as f:
        json.dump(clean, f, indent=2)


def apply_env_overrides(prefs):
    """Override preference values with environment variables when set."""
    env_map = {
        "GMAIL_ADDRESS": "gmail_address",
        "GMAIL_APP_PASSWORD": "gmail_app_password",
        "EMAIL_RECIPIENT": "email",
        "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
        "TELEGRAM_CHAT_ID": "telegram_chat_id",
        "APOLLO_API_KEY": "apollo_api_key",
        "LINKEDIN_EMAIL": "linkedin_email",
        "LINKEDIN_PASSWORD": "linkedin_password",
    }
    for env_key, pref_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            prefs[pref_key] = val

    telegram_min = os.environ.get("TELEGRAM_MIN_SCORE")
    if telegram_min:
        try:
            prefs["telegram_min_score"] = int(telegram_min)
        except ValueError:
            pass

    return prefs


def interactive_setup():
    """
    Run first-time interactive setup or edit preferences.
    All questions are optional - press Enter to use defaults.
    """
    print("\n" + "=" * 60)
    print("  JOB SEARCH AGENT - Preferences Setup")
    print("  (Press Enter to skip any question and use default)")
    print("=" * 60 + "\n")

    existing = load_preferences() or DEFAULT_PREFS.copy()

    # Use questionary if available, otherwise fall back to input()
    try:
        import questionary
        use_questionary = True
    except ImportError:
        use_questionary = False

    def ask(prompt, default):
        if use_questionary:
            result = questionary.text(prompt, default=str(default)).ask()
        else:
            shown = str(default)
            result = input(f"{prompt} [{shown}]: ").strip()
        return result if result else str(default)

    # Question 1: Job titles
    titles_default = ", ".join(existing.get("job_titles", DEFAULT_PREFS["job_titles"]))
    titles_input = ask("What job titles are you looking for?", titles_default)
    job_titles = [t.strip() for t in titles_input.split(",") if t.strip()]

    # Question 2: Locations
    locs_default = ", ".join(existing.get("locations", DEFAULT_PREFS["locations"]))
    locs_input = ask("Preferred job locations?", locs_default)
    locations = [l.strip() for l in locs_input.split(",") if l.strip()]

    # Question 3: Industries
    ind_default = ", ".join(existing.get("industries", DEFAULT_PREFS["industries"]))
    ind_input = ask("Industries of interest?", ind_default)
    industries = [i.strip() for i in ind_input.split(",") if i.strip()]

    # Question 4: Top jobs count
    top_default = existing.get("top_jobs_per_digest", DEFAULT_PREFS["top_jobs_per_digest"])
    top_input = ask("How many top jobs per digest? (3-10)", str(top_default))
    try:
        top_jobs = max(3, min(10, int(top_input)))
    except ValueError:
        top_jobs = int(top_default)

    # Question 5: Digest time
    time_default = existing.get("digest_time", DEFAULT_PREFS["digest_time"])
    digest_time = ask("What time should daily digest be sent?", time_default)

    # Question 6: Email
    email_default = existing.get("email", DEFAULT_PREFS["email"])
    email = ask("Your email for receiving digests? (leave blank for HTML only)", email_default or "")

    prefs = {
        "job_titles": job_titles,
        "locations": locations,
        "industries": industries,
        "top_jobs_per_digest": top_jobs,
        "digest_time": digest_time,
        "email": email,
    }

    # Show confirmation
    print("\n" + "-" * 60)
    print("  Your Preferences:")
    print("-" * 60)
    print(f"  Job Titles:     {', '.join(prefs['job_titles'])}")
    print(f"  Locations:      {', '.join(prefs['locations'])}")
    print(f"  Industries:     {', '.join(prefs['industries'])}")
    print(f"  Jobs/Digest:    {prefs['top_jobs_per_digest']}")
    print(f"  Digest Time:    {prefs['digest_time']}")
    print(f"  Email:          {prefs['email'] or '(HTML only - no email)'}")
    print("-" * 60)

    if use_questionary:
        confirm = questionary.confirm("Save these preferences?", default=True).ask()
    else:
        confirm_input = input("\nSave these preferences? [Y/n]: ").strip().lower()
        confirm = confirm_input != "n"

    if confirm:
        save_preferences(prefs)
        print("\nPreferences saved to user_preferences.json")
    else:
        print("\nPreferences not saved.")

    return prefs


# =============================================================================
# Main Pipeline
# =============================================================================

def run_pipeline(config, preferences):
    """
    Execute the full job search pipeline:
    1. Scrape all portals
    2. Analyze and score jobs
    3. Store in database
    4. Generate digest
    """
    logger = logging.getLogger(__name__)
    start_time = datetime.now()

    print("\n" + "=" * 60)
    print("  JOB SEARCH AGENT - Running Pipeline")
    print(f"  {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    job_titles = preferences.get("job_titles", DEFAULT_PREFS["job_titles"])
    locations = preferences.get("locations", DEFAULT_PREFS["locations"])
    top_n = preferences.get("top_jobs_per_digest", 5)

    # --- Step 1: Scrape ---
    print("\n[1/4] Scraping job portals...")

    def scrape_progress(portal, status, count, done, total):
        icon = "+" if status == "success" else "x"
        print(f"  [{icon}] {portal.capitalize()}: {count} jobs ({done}/{total} portals)")

    all_jobs, portal_results = scrape_all_portals(
        job_titles, locations, config, progress_callback=scrape_progress
    )
    print(f"\n  Total raw jobs: {len(all_jobs)}")

    if not all_jobs:
        print("\n  No jobs found from any portal. Check logs for details.")
        print("  This may be due to anti-scraping protections.")
        print("  Try running again later or adjusting portal settings in config.json.")
        logger.warning("No jobs found from any portal")
        # Still generate an empty digest
        stats = get_comprehensive_stats()
        open_browser = config.get("digest", {}).get("open_in_browser", True)
        html_path, txt_path = generate_digest([], portal_results, preferences, stats, open_browser)
        print(f"\n  Empty digest saved to: {html_path}")
        return

    # --- Step 2: Analyze ---
    print("\n[2/4] Analyzing and scoring jobs...")

    def analyze_progress(current, total, role, score):
        if current % 5 == 0 or current == total:
            print(f"  Analyzed {current}/{total} jobs...")

    qualified_jobs, all_analyzed = analyze_jobs(
        all_jobs, preferences, config, progress_callback=analyze_progress
    )
    print(f"  Qualified jobs (score >= {config.get('scoring', {}).get('min_relevance_score', 65)}): {len(qualified_jobs)}")

    # --- Step 3: Store in DB ---
    print("\n[3/4] Storing jobs in database...")
    # Add job_id to each job
    for job in all_analyzed:
        job["job_id"] = generate_job_id(
            job["portal"], job["company"], job["role"], job.get("location", "")
        )
    inserted, skipped = insert_jobs_bulk(all_analyzed)
    print(f"  Inserted: {inserted}, Duplicates skipped: {skipped}")

    # Get top N for digest
    digest_jobs = qualified_jobs[:top_n]

    # --- Step 4: Generate Digest ---
    print("\n[4/4] Generating digest...")
    stats = get_comprehensive_stats()
    open_browser = config.get("digest", {}).get("open_in_browser", True)
    html_path, txt_path = generate_digest(digest_jobs, portal_results, preferences, stats, open_browser)

    # Mark as sent
    sent_ids = [j.get("job_id") for j in digest_jobs if j.get("job_id")]
    if sent_ids:
        mark_sent_in_digest(sent_ids)

    # --- Step 5: Export top jobs to career-ops pipeline ---
    try:
        export_to_pipeline(digest_jobs, max_jobs=top_n)
    except Exception as e:
        logger.warning("queue_exporter failed (non-fatal): %s", e)

    elapsed = (datetime.now() - start_time).total_seconds()

    # Summary
    succeeded = sum(1 for r in portal_results.values() if r["status"] == "success")
    failed = sum(1 for r in portal_results.values() if r["status"] == "failed")

    print("\n" + "=" * 60)
    print("  PIPELINE COMPLETE")
    print("=" * 60)
    print(f"  Portals: {succeeded} succeeded, {failed} failed")
    print(f"  Jobs found: {len(all_jobs)}")
    print(f"  Jobs qualified: {len(qualified_jobs)}")
    print(f"  Jobs in digest: {len(digest_jobs)}")
    print(f"  Time elapsed: {elapsed:.1f}s")
    print(f"\n  HTML digest: {html_path}")
    print(f"  TXT digest:  {txt_path}")
    print("=" * 60 + "\n")

    logger.info(
        "Pipeline complete: %d portals (%d ok, %d fail), %d jobs found, "
        "%d qualified, %d in digest. Took %.1fs.",
        len(portal_results), succeeded, failed,
        len(all_jobs), len(qualified_jobs), len(digest_jobs), elapsed,
    )


# =============================================================================
# CLI Commands
# =============================================================================

def cmd_view_stats():
    """Display comprehensive job search statistics."""
    init_db()
    stats = get_comprehensive_stats()

    print("\n" + "=" * 60)
    print("  JOB SEARCH STATISTICS")
    print("=" * 60)
    print(f"\n  Total jobs tracked:  {stats['total_jobs']}")
    print(f"  Jobs found today:    {stats['jobs_today']}")
    print(f"  Jobs this week:      {stats['jobs_this_week']}")
    print(f"  Applied to:          {stats['applied_count']}")
    print(f"  Saved for later:     {stats['saved_count']}")

    if stats["portal_stats"]:
        print("\n  Jobs per Portal:")
        for portal, count in stats["portal_stats"].items():
            print(f"    {portal:15s} {count:4d} jobs")

    if stats["top_companies"]:
        print("\n  Top 5 Companies:")
        for company, count in stats["top_companies"]:
            print(f"    {company:30s} {count:3d} jobs")

    if stats["top_roles"]:
        print("\n  Top 5 Roles:")
        for role, count in stats["top_roles"]:
            print(f"    {role:40s} {count:3d}")

    print("\n" + "=" * 60 + "\n")


def cmd_portal_stats():
    """Show which portals return the best quality jobs."""
    init_db()
    portal_stats = get_portal_quality_stats()

    if not portal_stats:
        print("\nNo portal data yet. Run the agent first.\n")
        return

    print("\n" + "=" * 60)
    print("  PORTAL QUALITY STATISTICS")
    print("=" * 60)
    print(f"\n  {'Portal':<15} {'Total':<8} {'Avg Score':<12} {'Best':<8} {'Quality*':<8}")
    print("  " + "-" * 51)

    for p in portal_stats:
        print(
            f"  {p['portal']:<15} {p['total_jobs']:<8} "
            f"{p['avg_score']:<12} {p['max_score']:<8} {p['quality_jobs']:<8}"
        )

    print("\n  * Quality = jobs with relevance score >= 65")
    print("=" * 60 + "\n")


def cmd_last_digest():
    """Open the most recent HTML digest in the browser."""
    path = get_latest_digest()
    if path:
        print(f"\nOpening: {path}")
        webbrowser.open(f"file://{path}")
    else:
        print("\nNo digests found. Run the agent first.")


# =============================================================================
# Entry point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Job Search Agent - Automated multi-portal job scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    Run once and generate digest
  python main.py --schedule         Run daily at scheduled time
  python main.py --edit-preferences Change your search preferences
  python main.py --view-stats       View job search statistics
  python main.py --portal-stats     Compare portal quality
  python main.py --last-digest      Open most recent digest
        """,
    )
    parser.add_argument("--schedule", action="store_true", help="Setup daily scheduling")
    parser.add_argument("--edit-preferences", action="store_true", help="Modify user preferences")
    parser.add_argument("--view-stats", action="store_true", help="Show job search statistics")
    parser.add_argument("--manual-run", action="store_true", help="Run immediately")
    parser.add_argument("--last-digest", action="store_true", help="Open most recent digest")
    parser.add_argument("--portal-stats", action="store_true", help="Show portal quality stats")
    args = parser.parse_args()

    # Load config
    config = load_config()

    # Setup logging
    logger = setup_logging(config)
    logger.info("Job Search Agent started with args: %s", vars(args))

    # Initialize database
    init_db()

    # Handle stat/view commands that don't need preferences
    if args.view_stats:
        cmd_view_stats()
        return

    if args.portal_stats:
        cmd_portal_stats()
        return

    if args.last_digest:
        cmd_last_digest()
        return

    # Handle preferences
    if args.edit_preferences:
        interactive_setup()
        return

    # Load or create preferences
    preferences = load_preferences()
    if preferences is None:
        print("First run detected. Let's set up your preferences.")
        preferences = interactive_setup()
        if preferences is None:
            preferences = DEFAULT_PREFS.copy()
            save_preferences(preferences)

    # Schedule mode
    if args.schedule:
        def scheduled_run():
            run_pipeline(config, preferences)

        setup_scheduler(scheduled_run, preferences)
        return

    # Default: run pipeline once
    run_pipeline(config, preferences)


if __name__ == "__main__":
    main()
