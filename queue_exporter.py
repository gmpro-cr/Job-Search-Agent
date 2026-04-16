"""
queue_exporter.py — Bridge between the Python job scraper and career-ops.

After each digest run, exports the top N qualifying jobs to
tmp_career_ops/data/pipeline.md so Claude Code (career-ops) can evaluate,
tailor CVs, and track applications — all within Claude Pro, no API cost.

Usage:
    Called automatically from main.py after digest generation.
    Can also be run standalone: python queue_exporter.py
"""

import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_FILE = os.path.join(BASE_DIR, "tmp_career_ops", "data", "pipeline.md")
PIPELINE_HEADER_MARKER = "<!-- Scraper appends entries below this line -->"


def _format_job_entry(job: dict) -> str:
    """Format a single job as a pipeline.md entry."""
    company = job.get("company", "Unknown Company")
    role = job.get("role", "Unknown Role")
    score = job.get("relevance_score", 0)
    portal = job.get("portal", "Unknown")
    date = datetime.now().strftime("%Y-%m-%d")
    url = job.get("apply_url", "")
    location = job.get("location", "Unknown")
    salary = job.get("salary") or "Not disclosed"
    skills = ", ".join(job.get("skills", [])[:8]) or "Not listed"
    desc = (job.get("job_description", "")[:300] or "").strip()
    if len(job.get("job_description", "")) > 300:
        desc += "..."

    lines = [
        f"## {company} — {role}",
        f"**Score:** {score}/100  **Portal:** {portal}  **Date:** {date}",
        f"**URL:** {url}",
        f"**Location:** {location}  **Salary:** {salary}",
        f"**Skills:** {skills}",
        "**Description:**",
        desc,
        "---",
        "",
    ]
    return "\n".join(lines)


def export_to_pipeline(jobs: list[dict], max_jobs: int = 5) -> int:
    """
    Append top N jobs to tmp_career_ops/data/pipeline.md.

    Only exports jobs not already in the pipeline (deduped by URL).
    Returns the number of jobs actually appended.
    """
    if not jobs:
        logger.info("queue_exporter: no jobs to export")
        return 0

    # Read existing pipeline to dedup by URL
    existing_urls: set[str] = set()
    try:
        with open(PIPELINE_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        for line in content.splitlines():
            if line.startswith("**URL:**"):
                url = line.replace("**URL:**", "").strip()
                if url:
                    existing_urls.add(url)
    except FileNotFoundError:
        content = ""
        logger.warning("queue_exporter: pipeline.md not found, will create")

    # Filter to top N, skip already-queued URLs
    to_export = []
    for job in jobs[:max_jobs * 3]:  # look ahead to find enough new ones
        url = job.get("apply_url", "")
        if url and url in existing_urls:
            continue
        to_export.append(job)
        if len(to_export) >= max_jobs:
            break

    if not to_export:
        logger.info("queue_exporter: all top jobs already in pipeline")
        return 0

    # Build new entries
    new_entries = "\n".join(_format_job_entry(j) for j in to_export)

    # Append after the marker
    if PIPELINE_HEADER_MARKER in content:
        updated = content + "\n" + new_entries
    else:
        # Marker missing — just append
        updated = content + "\n\n" + new_entries

    os.makedirs(os.path.dirname(PIPELINE_FILE), exist_ok=True)
    with open(PIPELINE_FILE, "w", encoding="utf-8") as f:
        f.write(updated)

    logger.info("queue_exporter: exported %d jobs to pipeline.md", len(to_export))
    print(f"\n[5/5] Exported {len(to_export)} jobs to career-ops pipeline →")
    print(f"       tmp_career_ops/data/pipeline.md")
    print(f"       Open Claude Code in tmp_career_ops/ and run: /career-ops pipeline")
    return len(to_export)


# ── Standalone run ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys

    sys.path.insert(0, BASE_DIR)

    # Load config and prefs
    config_path = os.path.join(BASE_DIR, "config.json")
    prefs_path = os.path.join(BASE_DIR, "user_preferences.json")

    config = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)

    prefs = {}
    if os.path.exists(prefs_path):
        with open(prefs_path) as f:
            prefs = json.load(f)

    top_n = prefs.get("top_jobs_per_digest", 5)

    # Pull top qualifying jobs from the DB
    from database import get_unsent_jobs
    jobs = get_unsent_jobs(limit=top_n * 3)

    exported = export_to_pipeline(jobs, max_jobs=top_n)
    print(f"Exported {exported} job(s) to pipeline.")
