"""
One-way bulk migration: local SQLite (jobs.db) → Neon Postgres.

Reads from the on-disk SQLite database at $DATA_DIR/jobs.db (or the repo
default) and copies all rows into the Postgres database pointed to by
DATABASE_URL. Idempotent — uses ON CONFLICT DO NOTHING so re-running just
fills in new rows. After data is copied, autoincrement sequences for the
two SERIAL tables (users, user_reminders) are bumped to MAX(id)+1 so
future inserts don't collide with the migrated IDs.

Applies a 60-day cutoff on job_listings to keep within Neon's 0.5GB free
tier. Override with --max-age-days; pass 0 to migrate everything.

Usage:
  python -m scripts.sqlite_to_neon
  python -m scripts.sqlite_to_neon --max-age-days 0     # migrate all
  python -m scripts.sqlite_to_neon --batch-size 500
  SQLITE_PATH=/path/to/jobs.db python -m scripts.sqlite_to_neon

Prerequisites:
  - DATABASE_URL points at the target Neon DB.
  - init_db() has already been run on Neon (see scripts.init_neon).
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

# database.py loads .env on import so DATABASE_URL is populated.
import database  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sqlite_to_neon")

DEFAULT_SQLITE_PATH = os.environ.get(
    "SQLITE_PATH", os.path.join(_BASE, "jobs.db")
)


# Tables to copy. Order matters for FK-like dependencies (users before
# user_* children; job_listings before user_job_state).
_TABLES = [
    "users",
    "job_listings",
    "outreach_queue",
    "user_preferences",
    "user_cv_data",
    "user_reminders",
    "user_job_state",
]

# Composite-key conflict targets per table — used by ON CONFLICT DO NOTHING.
_CONFLICT_TARGETS = {
    "users": "(id)",
    "job_listings": "(job_id)",
    "outreach_queue": "(job_id)",
    "user_preferences": "(user_id)",
    "user_cv_data": "(user_id)",
    "user_reminders": "(user_id, reminder_id)",
    "user_job_state": "(user_id, job_id)",
}


def _sqlite_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(sqlite_cur, table):
    sqlite_cur.execute(f"PRAGMA table_info({table})")
    return [r["name"] for r in sqlite_cur.fetchall()]


def _migrate_table(sqlite_cur, pg_cur, table, batch_size, where_clause=None):
    cols = _columns(sqlite_cur, table)
    if not cols:
        logger.warning("Table %s has no columns; skipping", table)
        return 0
    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    conflict = _CONFLICT_TARGETS.get(table)
    on_conflict = f" ON CONFLICT {conflict} DO NOTHING" if conflict else ""
    insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}){on_conflict}"

    select_sql = f"SELECT {col_list} FROM {table}"
    if where_clause:
        select_sql += f" WHERE {where_clause}"
    sqlite_cur.execute(select_sql)

    total = 0
    batch: list = []
    while True:
        rows = sqlite_cur.fetchmany(batch_size)
        if not rows:
            break
        for r in rows:
            batch.append(tuple(r[c] for c in cols))
        pg_cur.executemany(insert_sql, batch)
        total += len(batch)
        logger.info("  %s: copied %d (cumulative)", table, total)
        batch = []
    return total


def _bump_sequences(pg_conn, pg_cur):
    """
    After a bulk migration that preserved IDs, advance the SERIAL sequences
    so the next auto-generated id won't collide with migrated rows.
    """
    for table, column in (("users", "id"), ("user_reminders", "id")):
        pg_cur.execute(
            f"SELECT COALESCE(MAX({column}), 0) AS m FROM {table}"
        )
        m = pg_cur.fetchone()["m"]
        if m and m > 0:
            # Use ? placeholder so the _PgCursor adapter rewrites it for psycopg.
            seq_sql = f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), ?, true)"
            pg_cur.execute(seq_sql, (m,))
            logger.info("Bumped %s_%s_seq to %s", table, column, m)
    pg_conn.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-path", default=DEFAULT_SQLITE_PATH)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-age-days", type=int, default=60,
                        help="Drop job_listings older than this. 0 disables.")
    args = parser.parse_args()

    if not database.USE_POSTGRES:
        logger.error("DATABASE_URL is not set to Postgres. Aborting.")
        sys.exit(1)
    if not os.path.exists(args.sqlite_path):
        logger.error("SQLite file not found: %s", args.sqlite_path)
        sys.exit(1)

    logger.info("Source SQLite: %s", args.sqlite_path)
    logger.info("Target Postgres: %s", database.DATABASE_URL.split("@")[-1])

    database.init_db()  # idempotent; ensure target schema exists

    sqlite_conn = _sqlite_conn(args.sqlite_path)
    sqlite_cur = sqlite_conn.cursor()
    pg_conn = database.get_connection()
    pg_cur = pg_conn.cursor()

    grand_total = 0
    try:
        for table in _TABLES:
            where = None
            if table == "job_listings" and args.max_age_days > 0:
                cutoff = (datetime.now() - timedelta(days=args.max_age_days)).isoformat()
                where = f"date_found >= '{cutoff}'"
                logger.info("job_listings cutoff: %s", cutoff)
            elif table == "user_job_state" and args.max_age_days > 0:
                # Only migrate state for jobs we kept.
                cutoff = (datetime.now() - timedelta(days=args.max_age_days)).isoformat()
                where = (
                    f"job_id IN (SELECT job_id FROM job_listings "
                    f"WHERE date_found >= '{cutoff}')"
                )
            logger.info("→ %s", table)
            count = _migrate_table(sqlite_cur, pg_cur, table, args.batch_size, where)
            pg_conn.commit()
            logger.info("%s: %d rows", table, count)
            grand_total += count

        _bump_sequences(pg_conn, pg_cur)
        logger.info("DONE. Migrated %d rows.", grand_total)
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()
