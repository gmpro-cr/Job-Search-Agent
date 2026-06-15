"""Calibration helper for the deterministic+embedding scorer.

Scores a sample of stored jobs against the owner's CV + preferences and prints
the score distribution so you can pick a sensible config.json
`scoring.min_relevance_score`. Run after backfilling embeddings so the
distribution reflects the blended (deterministic + semantic) scale:

    python -m scripts.calibrate_scores            # default 1000-job sample

Uses whatever DB DATABASE_URL points at (Neon in production).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db          # noqa: E402
from scorer import score_job   # noqa: E402
from embeddings import from_blob  # noqa: E402


def main(sample=1000):
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT prefs_json FROM user_preferences ORDER BY user_id LIMIT 1")
    row = cur.fetchone()
    prefs = json.loads(row["prefs_json"]) if row else {}
    cur.execute("SELECT cv_json, cv_embedding FROM user_cv_data ORDER BY user_id LIMIT 1")
    cvrow = cur.fetchone()
    cv = json.loads(cvrow["cv_json"]) if cvrow and cvrow["cv_json"] else {}
    profile_vec = from_blob(cvrow["cv_embedding"]) if cvrow and cvrow["cv_embedding"] else None
    print(f"profile_vec present: {profile_vec is not None}")

    cur.execute(
        "SELECT role, company, job_description, location, remote_status, embedding "
        "FROM job_listings ORDER BY date_found DESC LIMIT ?", (sample,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    scores = []
    for r in rows:
        jv = from_blob(r.get("embedding")) if r.get("embedding") else None
        s, _ = score_job(r, cv, prefs, job_vec=jv, profile_vec=profile_vec)
        scores.append(s)
    scores.sort()
    n = len(scores) or 1

    def pct(p):
        return scores[min(n - 1, int(n * p))]

    print(f"sampled {n} jobs  min={scores[0]}  max={scores[-1]}  mean={sum(scores)/n:.1f}")
    for p in (0.50, 0.75, 0.90, 0.95, 0.99):
        print(f"  p{int(p*100)}: {pct(p)}")
    for thr in (40, 50, 55, 60, 65, 70):
        print(f"  >= {thr}: {sum(1 for s in scores if s >= thr)} jobs")


if __name__ == "__main__":
    main()
