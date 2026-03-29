"""
autoresearch/seed.py — Run ONCE to create testset.json.
Samples 30 diverse jobs from jobs.db, scores them with a high-quality LLM,
and saves the frozen ground truth to testset.json.

Usage:
    python autoresearch/seed.py [--n 30] [--force]
"""
import argparse
import json
import logging
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

_BASE         = os.path.dirname(os.path.abspath(__file__))
TESTSET_PATH  = os.path.join(_BASE, "testset.json")
BASELINE_PATH = os.path.join(_BASE, "baseline.json")


def _sample_jobs(n: int) -> list:
    """Sample n diverse jobs from the DB (spread across score bands)."""
    from database import get_connection
    conn = get_connection()
    cur  = conn.cursor()

    jobs = []
    # 10 high-scoring, 10 mid, 10 low
    for score_min, score_max in [(70, 101), (40, 70), (0, 40)]:
        cur.execute(
            """SELECT job_id, role, company, job_description, relevance_score
               FROM job_listings
               WHERE relevance_score >= ? AND relevance_score < ?
                 AND (hidden = 0 OR hidden IS NULL)
                 AND job_description IS NOT NULL AND job_description != ''
               ORDER BY RANDOM() LIMIT ?""",
            (score_min, score_max, n // 3)
        )
        jobs.extend([dict(r) for r in cur.fetchall()])

    conn.close()
    random.shuffle(jobs)
    logger.info("Sampled %d jobs from DB", len(jobs))
    return jobs


def _ground_truth_score(job: dict, cv_skills: str, cv_summary: str) -> dict:
    """Score one job with a detailed LLM prompt to establish ground truth."""
    from agent.llm import call_llm_json

    role    = job.get("role", "")
    company = job.get("company", "")
    jd      = (job.get("job_description") or "")[:1000]

    prompt = f"""You are an expert career advisor. Rate how well this job matches the candidate's CV.
Be precise and use the full 0-100 range. Penalise hard mismatches (wrong domain, seniority gap) heavily.

JOB:
Role: {role}
Company: {company}
Description: {jd}

CANDIDATE:
Skills: {cv_skills}
Background: {cv_summary}

Return ONLY valid JSON:
{{"score": <integer 0-100>, "reason": "<2 sentences explaining the rating>"}}"""

    result = call_llm_json(prompt)
    score  = max(0, min(100, int(result.get("score", 50))))
    return {"score": score, "reason": result.get("reason", "")}


def seed(n: int = 30, force: bool = False) -> str:
    """Create testset.json. Returns path to created file."""
    if os.path.exists(TESTSET_PATH) and not force:
        logger.info("testset.json already exists. Use --force to reseed.")
        return TESTSET_PATH

    from analyzer import load_cv_data
    cv_data    = load_cv_data() or {}
    cv_skills  = ", ".join((cv_data.get("skills") or [])[:20])
    cv_summary = (cv_data.get("raw_text") or "")[:600]

    if not cv_skills:
        logger.error("No CV data found. Upload your CV first at /cv")
        sys.exit(1)

    jobs    = _sample_jobs(n)
    testset = []

    for i, job in enumerate(jobs, 1):
        logger.info("[%d/%d] Scoring: %s @ %s", i, len(jobs), job["role"], job["company"])
        try:
            gt = _ground_truth_score(job, cv_skills, cv_summary)
        except Exception as e:
            logger.warning("  Failed: %s — skipping", e)
            continue

        testset.append({
            "job_id":               job["job_id"],
            "role":                 job["role"],
            "company":              job["company"],
            "job_description":      (job.get("job_description") or "")[:800],
            "db_relevance_score":   job.get("relevance_score", 0),
            "ground_truth_score":   gt["score"],
            "ground_truth_reason":  gt["reason"],
        })

    with open(TESTSET_PATH, "w", encoding="utf-8") as f:
        json.dump(testset, f, indent=2)

    logger.info("Saved %d jobs to %s", len(testset), TESTSET_PATH)
    return TESTSET_PATH


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n",     type=int, default=30)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    seed(args.n, args.force)
