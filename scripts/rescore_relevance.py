"""Bulk-rescore job_listings.relevance_score with the current scorer.

Stored relevance_score values were written by whichever scorer was live at
scrape time. After a scoring change the corpus holds a mix of scales, which
skews /jobs ordering, dashboard counts, reminder thresholds and the
dedup_jobs tiebreak. This rewrites every row with the current scorer.

Deterministic component only — per-job embeddings are not stored on
job_listings, so the semantic lift in scorer.score_job cannot be reproduced
here. New scrapes still apply it; this puts stored rows on the right scale.

Dry run by default:
    python -m scripts.rescore_relevance
    python -m scripts.rescore_relevance --apply
    python -m scripts.rescore_relevance --apply --batch 500
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_connection            # noqa: E402
from scorer import deterministic_score         # noqa: E402

BATCH = 500


def _load_profile():
    """CV + preferences for the owner, preferring the DB over the JSON files."""
    from main import load_preferences
    from analyzer import load_cv_data
    cv = load_cv_data() or {}
    prefs = load_preferences() or {}
    if not cv.get("skills"):
        print("WARNING: no CV skills loaded — cv_skills band will score 0.")
    return cv, prefs


def _histogram(values):
    buckets = collections.Counter()
    for v in values:
        buckets[min(int(v or 0) // 10 * 10, 100)] += 1
    return buckets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the new scores (default: dry run)")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    cv, prefs = _load_profile()

    conn = get_connection()
    cursor = conn.cursor()
    sql = ("SELECT job_id, role, company, location, job_description, "
           "remote_status, relevance_score FROM job_listings")
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    cursor.execute(sql)
    rows = [dict(r) for r in cursor.fetchall()]
    print(f"loaded {len(rows)} rows from job_listings")

    updates, old, new = [], [], []
    for r in rows:
        before = r.get("relevance_score") or 0
        after, _ = deterministic_score(r, cv, prefs)
        old.append(before)
        new.append(after)
        if after != before:
            updates.append((after, r["job_id"]))

    print(f"\n{len(updates)} of {len(rows)} rows would change "
          f"({len(updates) / max(len(rows), 1) * 100:.1f}%)")
    print(f"mean  {sum(old)/max(len(old),1):.1f} -> {sum(new)/max(len(new),1):.1f}")
    for cut in (55, 65, 75, 85):
        o = sum(1 for v in old if v >= cut) / max(len(old), 1) * 100
        n = sum(1 for v in new if v >= cut) / max(len(new), 1) * 100
        print(f"  >= {cut}: {o:5.1f}%  ->  {n:5.1f}%")

    ho, hn = _histogram(old), _histogram(new)
    print(f"\n{'bucket':>8} {'old':>7} {'new':>7}")
    for b in sorted(set(ho) | set(hn)):
        print(f"{b:>5}-{b+9:<3} {ho.get(b,0):>7} {hn.get(b,0):>7}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
        conn.close()
        return 0

    print(f"\napplying {len(updates)} updates in batches of {args.batch} ...")
    # Write through a FRESH cursor. Reusing the SELECT cursor left a tail of
    # updates unapplied (the first production run needed a second pass), because
    # committing mid-iteration on the cursor that still held the read's result
    # set dropped writes silently.
    write_cur = conn.cursor()
    done = 0
    for i in range(0, len(updates), args.batch):
        batch = updates[i:i + args.batch]
        for score, job_id in batch:
            write_cur.execute(
                "UPDATE job_listings SET relevance_score = ? WHERE job_id = ?",
                (score, job_id))
        conn.commit()
        done += len(batch)
        print(f"  committed {done}/{len(updates)}")
    conn.close()
    print(f"done. re-run without --apply to confirm 0 rows differ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
