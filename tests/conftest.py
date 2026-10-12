"""Keep the test suite off production.

database.py (and main/app/blob_storage) call load_dotenv() at import, so a
developer .env holding the Neon DATABASE_URL used to point the tests at the
production database. test_routine_runner's fixture runs
`DELETE FROM user_reminders / job_listings / user_job_state`, and in
September 2026 that wiped every saved routine, all per-user job state and the
job table in production.

load_dotenv() never overrides a variable that is already set (even to ""), so
blanking the production settings here, before any test module imports the
app, wins over .env. The session then aborts outright if Postgres is still
selected.
"""
import os
import tempfile

import pytest

for _var in ("DATABASE_URL", "DATABASE_URL_DIRECT", "BLOB_READ_WRITE_TOKEN",
             "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "EMAIL_RECIPIENT",
             "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ[_var] = ""
# A throwaway DATA_DIR so the local jobs.db is never touched either.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="jsa-tests-")

import database  # noqa: E402

if database.USE_POSTGRES:
    pytest.exit("Refusing to run tests against Postgres (DATABASE_URL is set).",
                returncode=2)
