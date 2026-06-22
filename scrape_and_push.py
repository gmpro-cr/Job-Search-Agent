#!/usr/bin/env python3
"""
scrape_and_push.py - Standalone scraper that runs on GitHub Actions.

Scrapes all job portals, analyzes/scores jobs, emails per-routine digests,
then writes results to data/latest_scrape.json for the local app to import.

Required env vars:
    GMAIL_ADDRESS         - sender Gmail address
    GMAIL_APP_PASSWORD    - Gmail App Password (not your Google password)
    EMAIL_RECIPIENT       - recipient email address
"""

import json
import logging
import os
import sys
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

from main import load_config, load_preferences, DEFAULT_PREFS, apply_env_overrides
from scrapers import scrape_all_portals
from analyzer import analyze_jobs
from database import generate_job_id, init_db, insert_jobs_bulk, delete_old_jobs, \
    get_all_user_targets, get_all_users_with_cv_data, get_unscored_jobs_for_user, \
    bulk_set_user_cv_scores, select_digest_jobs, mark_sent_in_digest, \
    set_job_embeddings_bulk, set_cv_embedding, get_user_by_email
from email_notifier import send_job_email

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _safe_embed(texts):
    """Embed texts, returning a list of vectors — or [None]*len on any failure
    (e.g. sentence-transformers not installed in a local run). Keeps the cron
    working in deterministic-only mode when the model is unavailable."""
    if not texts:
        return []
    try:
        from embeddings import embed_texts
        return embed_texts(texts)
    except Exception as e:
        logger.warning("Embeddings unavailable (%s) — scoring deterministic-only", e)
        return [None] * len(texts)


def embed_jobs(jobs):
    """Attach an embedding vector to each job under job['_vec']."""
    texts = [
        f"{j.get('role', '')}. {j.get('company', '')}. {(j.get('job_description') or '')[:2000]}"
        for j in jobs
    ]
    vecs = _safe_embed(texts)
    for j, v in zip(jobs, vecs):
        j["_vec"] = v
    return jobs


def build_profile_vec(cv_data, preferences):
    """Embed a synthesized profile text (titles + industries + CV skills/summary).
    Returns None when there is nothing to embed or the model is unavailable."""
    cv_data = cv_data or {}
    preferences = preferences or {}
    parts = [
        " ".join(preferences.get("job_titles", []) or []),
        " ".join(preferences.get("industries", []) or []),
        " ".join(cv_data.get("skills", []) or []),
        cv_data.get("summary", "") or "",
    ]
    text = " ".join(p for p in parts if p).strip()
    if not text:
        return None
    vecs = _safe_embed([text])
    return vecs[0] if vecs else None

DATA_DIR = os.path.join(BASE_DIR, "data")
SCRAPE_OUTPUT = os.path.join(DATA_DIR, "latest_scrape.json")


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    # Run the idempotent schema migration up front so the embedding columns
    # exist before Phase 2 writes the owner profile vector (set_cv_embedding).
    init_db()

    config = load_config()
    preferences = apply_env_overrides(load_preferences() or DEFAULT_PREFS.copy())

    # Multi-user mode: union all users' job titles + locations from DB.
    # Falls back to the JSON preferences file if no users have saved prefs yet.
    db_titles, db_locs = get_all_user_targets()
    default_titles = preferences.get("job_titles") or DEFAULT_PREFS["job_titles"]
    default_locs   = preferences.get("locations")  or DEFAULT_PREFS["locations"]
    job_titles = db_titles or default_titles
    locations  = db_locs  or default_locs

    top_n = preferences.get("top_jobs_per_digest", 10)
    logger.info("Scraper targets — %d titles, %d locations across all users", len(job_titles), len(locations))

    # --- Phase 1: Scrape ---
    logger.info("Scraping %d titles across %d locations...", len(job_titles), len(locations))
    all_jobs, portal_results = scrape_all_portals(job_titles, locations, config)

    for portal, result in portal_results.items():
        logger.info("  %s: %s (%d jobs)", portal, result.get("status"), result.get("count", 0))

    if not all_jobs:
        logger.warning("No jobs scraped. Exiting.")
        return

    logger.info("Total raw jobs scraped: %d", len(all_jobs))

    # --- Phase 2: Analyze ---
    # In multi-user mode the CV lives in the DB, not the JSON file that
    # load_cv_data() reads in CLI/Actions context. Pull the owner user's CV +
    # prefs so scoring uses the real profile instead of falling back to
    # keyword-only (which scores everything < 65 → "0 jobs" digest).
    owner_cv: dict = {}
    owner_prefs = preferences
    owner_user_id = None
    try:
        all_cv_users = get_all_users_with_cv_data()
        # Pick the ACTUAL owner by OWNER_EMAIL — not an arbitrary first row.
        # get_all_users_with_cv_data() has no ORDER BY, so [0] could be any
        # user (e.g. one whose titles are "Summer Trainee"), which would score
        # the owner's PM jobs against the wrong profile -> ~0 qualified.
        owner_email = os.environ.get("OWNER_EMAIL", "").strip().lower()
        owner_row = None
        if owner_email:
            owner_user = get_user_by_email(owner_email)
            if owner_user:
                owner_row = next(
                    (u for u in all_cv_users if u["user_id"] == owner_user["id"]), None)
        if owner_row is None and all_cv_users:
            owner_row = all_cv_users[0]
            logger.warning(
                "OWNER_EMAIL %r not matched to a CV user — falling back to first CV user %s",
                owner_email or "(unset)", owner_row["user_id"])
        if owner_row:
            owner_cv = owner_row["cv_data"] or {}
            owner_user_id = owner_row["user_id"]
            if owner_row.get("prefs"):
                owner_prefs = owner_row["prefs"]
            logger.info("Digest scoring against user %d (owner_email=%s), titles=%s",
                        owner_user_id, owner_email or "(unset)",
                        (owner_prefs or {}).get("job_titles"))
        else:
            logger.warning("No users with CV found in DB — digest will use keyword-only scoring")
    except Exception as e:
        logger.warning("Could not load owner CV from DB: %s", e)

    # Embed jobs + owner profile so scoring can blend in semantic similarity.
    # Degrades to deterministic-only if the model isn't available.
    logger.info("Embedding jobs + owner profile...")
    all_jobs = embed_jobs(all_jobs)
    profile_vec = build_profile_vec(owner_cv, owner_prefs)
    if owner_user_id is not None and profile_vec is not None:
        try:
            set_cv_embedding(owner_user_id, profile_vec)
        except Exception as e:
            logger.warning("set_cv_embedding failed: %s", e)

    logger.info("Analyzing and scoring jobs...")
    qualified_jobs, all_analyzed = analyze_jobs(
        all_jobs, owner_prefs, config, cv_data=owner_cv, profile_vec=profile_vec)
    logger.info("Analyzed %d jobs, %d qualified", len(all_analyzed), len(qualified_jobs))

    # --- Phase 3: Generate IDs ---
    for job in all_analyzed:
        job["job_id"] = generate_job_id(
            job.get("portal", "unknown"),
            job.get("company", ""),
            job.get("role", ""),
            job.get("location", ""),
        )

    # --- Phase 3b: Pick digest jobs, excluding ones already sent recently ---
    # qualified_jobs is sorted by score desc. Drop any job emailed in the last
    # 7 days so each digest surfaces fresh listings instead of repeating the
    # same top matches every run. job_ids are set in Phase 3 above.
    # Scope dedup to the owner user so it never leaks across users; the digest
    # email below goes to the owner. select_digest_jobs prefers fresh listings
    # but backfills with recently-sent ones so the digest is never empty when
    # few jobs qualify (e.g. LLM down -> keyword-only scoring).
    digest_jobs = select_digest_jobs(qualified_jobs, top_n, days=7, user_id=owner_user_id)
    digest_job_ids = [j.get("job_id") for j in digest_jobs if j.get("job_id")]
    logger.info(
        "Digest selection: %d qualified -> sending %d (top_n=%d)",
        len(qualified_jobs), len(digest_jobs), top_n,
    )

    # --- Phase 4: Write JSON for local app to import ---
    serializable_fields = [
        "job_id", "portal", "company", "role", "salary", "salary_currency",
        "location", "job_description", "apply_url", "relevance_score",
        "remote_status", "company_type", "date_posted",
        "experience_min", "experience_max", "salary_min", "salary_max",
        "company_size", "company_funding_stage", "company_glassdoor_rating",
    ]
    payload_jobs = []
    for job in all_analyzed:
        clean = {k: job[k] for k in serializable_fields if k in job and job[k] is not None}
        payload_jobs.append(clean)

    with open(SCRAPE_OUTPUT, "w") as f:
        json.dump(payload_jobs, f)
    logger.info("Wrote %d jobs to %s", len(payload_jobs), SCRAPE_OUTPUT)


    # --- Phase 6: Default email digest ---
    # Skip it when the owner has saved routines — Phase 7c sends per-routine
    # emails that reflect each routine's own keywords/locations. The default
    # digest's header comes from preferences (DEFAULT_PREFS in CI), so sending
    # both produced an email whose "Searching for" line didn't match the routine.
    owner_has_routines = False
    try:
        from database import get_user_reminders
        owner_has_routines = owner_user_id is not None and any(
            r.get("enabled", True) and (r.get("keyword") or "").strip()
            for r in (get_user_reminders(owner_user_id) or [])
        )
    except Exception as e:
        logger.warning("Could not check owner routines: %s", e)

    recipient = preferences.get("email", "").strip()
    gmail_addr = preferences.get("gmail_address", "").strip()
    gmail_pass = preferences.get("gmail_app_password", "").strip()
    # Only mark the digest jobs as "sent" (Phase 7) if we actually emailed them.
    # Otherwise the owner's routine dedup (Phase 7c) would treat these fresh,
    # top-scoring jobs as already-delivered and email the owner nothing.
    default_digest_emailed = False
    if owner_has_routines:
        logger.info("Owner has saved routines — skipping default digest (routine emails cover it)")
    elif recipient and gmail_addr and gmail_pass:
        try:
            ok, err = send_job_email(recipient, digest_jobs, preferences)
            if ok:
                default_digest_emailed = True
                logger.info("Email digest sent to %s (%d jobs)", recipient, len(digest_jobs))
            else:
                logger.error("Email digest failed: %s", err)
        except Exception as e:
            logger.error("Failed to send email: %s", e)
    else:
        logger.warning("Email credentials not set — skipping email notification")

    # --- Phase 6b: Generate HTML digest and upload to Blob ---
    try:
        from collections import Counter
        from digest_generator import generate_digest
        company_counts = Counter(j.get("company", "") for j in all_analyzed if j.get("company"))
        role_counts    = Counter(j.get("role", "")    for j in all_analyzed if j.get("role"))
        digest_stats = {
            "top_companies":  company_counts.most_common(10),
            "top_roles":      role_counts.most_common(10),
            "jobs_today":     len(all_analyzed),
            "jobs_this_week": len(all_analyzed),
            "total_jobs":     len(all_analyzed),
        }
        generate_digest(digest_jobs, portal_results, owner_prefs,
                        digest_stats, open_browser=False)
        logger.info("HTML digest generated and uploaded to Blob")
    except Exception as e:
        logger.warning("Digest generation failed: %s", e)

    # --- Phase 7: Insert jobs + retention sweep ---
    init_db()
    insert_jobs_bulk(all_analyzed)
    # Persist job embedding vectors in ONE batched write (after insert, so the
    # rows exist). The old per-row loop opened a fresh Neon connection per job
    # (~2k connections), which stalled the run past the 90-min cron timeout
    # before the routine + dedup phase could finish.
    try:
        _embedded = set_job_embeddings_bulk(
            (job.get("job_id"), job.get("_vec")) for job in all_analyzed)
        if _embedded:
            logger.info("Stored embeddings for %d jobs", _embedded)
    except Exception as e:
        logger.warning("bulk embedding persist failed: %s", e)
    # Mark the jobs we just emailed as sent (AFTER insert, so the UPDATE hits
    # real rows) — this is what makes the next run's dedup work. Only do this
    # when the default digest was actually emailed; when the owner has routines
    # the default digest is skipped, and marking here would starve the owner's
    # routine email (Phase 7c) of the very jobs it should send.
    if digest_job_ids and default_digest_emailed:
        try:
            mark_sent_in_digest(digest_job_ids, user_id=owner_user_id)
            logger.info("Marked %d jobs as sent in digest", len(digest_job_ids))
        except Exception as e:
            logger.warning("mark_sent_in_digest failed: %s", e)
    try:
        purged = delete_old_jobs(days=7)
        logger.info(
            "Retention sweep: removed %d jobs, %d user_states, %d outreach drafts",
            purged["jobs"], purged["user_state"], purged["outreach"],
        )
    except Exception as e:
        logger.warning("Retention sweep failed: %s", e)

    # --- Phase 7b: Score new jobs for every user (incremental) ---
    # Only scores jobs the user hasn't seen yet — ~150 new jobs per run,
    # not the full 6k. Takes ~0.1s per user regardless of DB size.
    try:
        from analyzer import cv_score as _cv_score
        all_users = get_all_users_with_cv_data()
        logger.info("Scoring new jobs for %d users", len(all_users))
        for _u in all_users:
            _uid  = _u["user_id"]
            _cv   = _u["cv_data"]
            _pref = _u["prefs"]
            _unscored = get_unscored_jobs_for_user(_uid, limit=500)
            if not _unscored:
                continue
            _scores = {j["job_id"]: _cv_score(j, _cv, _pref) for j in _unscored}
            bulk_set_user_cv_scores(_uid, _scores)
            logger.info("  User %d: scored %d new jobs", _uid, len(_scores))
    except Exception as e:
        logger.warning("Per-user scoring failed: %s", e)

    # --- Phase 7c: Send per-routine digest emails (one email per saved routine) ---
    try:
        from routine_runner import run_routines
        rr = run_routines()
        logger.info("Routine emails: %d sent across %d routines (%d users)",
                    rr["emails_sent"], rr["routines"], rr["users"])
    except Exception as e:
        logger.warning("Routine run failed: %s", e)

    # --- Phase 7d: Funding news from public RSS sources (Google News + TechCrunch) ---
    # These work from the Actions IP (no Cloudflare). finsmes is NOT scraped here
    # (it 403s datacenter IPs) — it's refreshed locally via scripts/scrape_funding.py.
    try:
        from news_scraper import scrape_news_sources
        from database import insert_funding_bulk, delete_old_funding
        funding = scrape_news_sources()
        new = insert_funding_bulk(funding)
        delete_old_funding(days=120)
        logger.info("Funding news (RSS): %d scraped, %d new", len(funding), new)
    except Exception as e:
        logger.warning("Funding news scrape failed: %s", e)

    # --- Phase 8: Auto-discover hiring managers ---
    try:
        from hiring_managers_search import (
            get_new_hiring_managers,
            load_hm_contacts_dict,
            save_hm_contacts_dict,
            load_all_hm_contacts,
        )
        _role_kws = job_titles[:5]
        _location = (locations[0] if locations else "India")
        _existing = load_all_hm_contacts()
        new_hms = get_new_hiring_managers(
            sent_contacts=_existing,
            role_keywords=_role_kws,
            location=_location,
            target=5,
        )
        if new_hms:
            today = date.today().isoformat()
            hm_data = load_hm_contacts_dict()
            bucket = hm_data.setdefault("scraper_auto", [])
            for c in new_hms:
                c.setdefault("date_sent", today)
                bucket.append(c)
            save_hm_contacts_dict(hm_data)
            logger.info("Auto-HM discovery: added %d new contacts", len(new_hms))
        else:
            logger.info("Auto-HM discovery: no new contacts found")
    except Exception as e:
        logger.warning("Auto-HM discovery skipped: %s", e)

    # --- Phase 9: AI agent — contact enrichment + outreach drafts ---
    # Picks up freshly inserted jobs, looks up hiring manager contacts
    # (Apollo API if APOLLO_API_KEY is set, else falls back to portal-scraped
    # poster info), drafts cold emails, and queues them in outreach_queue.
    # Wrapped in try/except so a missing LLM key never breaks the scrape.
    try:
        from agent.graph import run_agent_pipeline
        agent_result = run_agent_pipeline(preferences, config)
        logger.info(
            "Agent pipeline complete — %d drafts queued",
            agent_result.get("queued_count", 0),
        )
        if agent_result.get("errors"):
            for err in agent_result["errors"]:
                logger.warning("Agent error: %s", err)
    except Exception as e:
        logger.warning("Agent pipeline skipped: %s", e)

    logger.info("Done.")


if __name__ == "__main__":
    main()
