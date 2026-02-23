"""
git_sync.py — Import scraped jobs from data/latest_scrape.json.

Called on app startup. If data/latest_scrape.json is newer than the
last recorded import (data/last_import.txt), imports all jobs using
the provided insert function.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def sync_from_scrape(base_dir, insert_fn):
    """
    Import jobs from data/latest_scrape.json if newer than last import.

    Args:
        base_dir: project root directory (str)
        insert_fn: callable(jobs: list) -> (inserted: int, skipped: int)

    Returns:
        (inserted, skipped) tuple, or None if no import was needed.
    """
    scrape_file = os.path.join(base_dir, "data", "latest_scrape.json")
    stamp_file = os.path.join(base_dir, "data", "last_import.txt")

    if not os.path.exists(scrape_file):
        logger.info("sync_from_scrape: no scrape file found, skipping")
        return None

    scrape_mtime = os.path.getmtime(scrape_file)

    last_import = 0.0
    if os.path.exists(stamp_file):
        try:
            with open(stamp_file) as f:
                last_import = float(f.read().strip())
        except (ValueError, OSError):
            pass

    if scrape_mtime <= last_import:
        logger.info("sync_from_scrape: already imported this scrape, skipping")
        return None

    try:
        with open(scrape_file) as f:
            jobs = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("sync_from_scrape: failed to read scrape file: %s", e)
        return None

    inserted, skipped = insert_fn(jobs)
    logger.info("sync_from_scrape: inserted=%d, skipped=%d", inserted, skipped)

    try:
        with open(stamp_file, "w") as f:
            f.write(str(scrape_mtime))
    except OSError as e:
        logger.warning("sync_from_scrape: could not write stamp file: %s", e)

    return inserted, skipped
