"""
Test isolation: tests always run against a throwaway SQLite DB, never Neon.

database.py and main.py call load_dotenv() on import, and .env holds the
production DATABASE_URL. Some tests wipe tables (test_routine_runner's
fixture deletes job_listings / user_job_state / user_reminders), so running
the suite against .env once destroyed production data (2026-10-24, and
before that 2026-09-23). load_dotenv never overrides a variable that is
already set, so pinning these here — before any test module imports
database — keeps .env out of the test run.
"""
import os
import tempfile

os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_URL_DIRECT"] = ""
os.environ["BLOB_READ_WRITE_TOKEN"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jsa-tests-")

import database  # noqa: E402

if database.USE_POSTGRES:
    raise RuntimeError("Refusing to run tests against Postgres — DATABASE_URL leaked into the test env.")
