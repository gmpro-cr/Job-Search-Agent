"""One-time backfill of embedding vectors for existing job_listings rows.

Run in GitHub Actions (where sentence-transformers is installed):

    python -m scripts.backfill_embeddings

Embeds every job whose `embedding` is NULL in batches until none remain.
Idempotent: re-running only touches rows still missing a vector.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_jobs_missing_embedding, set_job_embedding, init_db  # noqa: E402
from embeddings import embed_texts  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_embeddings")

BATCH = 200


def main():
    # Ensure the embedding column exists (idempotent migration) before querying
    # it — the embedding/cv_embedding columns are added by init_db().
    init_db()
    total = 0
    while True:
        rows = get_jobs_missing_embedding(limit=BATCH)
        if not rows:
            break
        texts = [
            f"{r.get('role', '')}. {r.get('company', '')}. {(r.get('job_description') or '')[:2000]}"
            for r in rows
        ]
        vecs = embed_texts(texts)
        for r, v in zip(rows, vecs):
            set_job_embedding(r["job_id"], v)
        total += len(rows)
        logger.info("Embedded %d jobs (running total %d)", len(rows), total)
        if len(rows) < BATCH:
            break
    logger.info("Backfill complete: %d jobs embedded", total)


if __name__ == "__main__":
    main()
