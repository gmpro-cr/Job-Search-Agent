"""Tests for git_sync.sync_from_scrape."""
import json
import os
import tempfile
import pytest

from git_sync import sync_from_scrape


def _write_scrape(directory, jobs):
    """Write a fake latest_scrape.json and return its path."""
    data_dir = os.path.join(directory, "data")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "latest_scrape.json")
    with open(path, "w") as f:
        json.dump(jobs, f)
    return path


def test_imports_jobs_from_scrape_file():
    """sync_from_scrape calls insert_fn with the jobs from the file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        jobs = [{"job_id": "abc", "role": "Engineer", "company": "Acme"}]
        _write_scrape(tmpdir, jobs)

        received = []
        def fake_insert(j):
            received.extend(j)
            return len(j), 0

        sync_from_scrape(tmpdir, fake_insert)
        assert len(received) == 1
        assert received[0]["job_id"] == "abc"


def test_skips_when_already_imported():
    """sync_from_scrape does NOT call insert_fn if last_import.txt is newer."""
    with tempfile.TemporaryDirectory() as tmpdir:
        jobs = [{"job_id": "xyz", "role": "Designer"}]
        scrape_path = _write_scrape(tmpdir, jobs)

        import time
        time.sleep(0.01)
        stamp_path = os.path.join(tmpdir, "data", "last_import.txt")
        scrape_mtime = os.path.getmtime(scrape_path)
        with open(stamp_path, "w") as f:
            f.write(str(scrape_mtime + 1))  # stamp is newer

        called = []
        def fake_insert(j):
            called.extend(j)
            return len(j), 0

        sync_from_scrape(tmpdir, fake_insert)
        assert called == [], "Should not re-import when stamp is newer than scrape"


def test_returns_none_when_no_file():
    """sync_from_scrape returns None gracefully when no scrape file exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        called = []
        result = sync_from_scrape(tmpdir, lambda j: called.append(j) or (0, 0))
        assert result is None
        assert called == []


def test_stamp_written_after_import():
    """sync_from_scrape writes last_import.txt with the scrape file's mtime."""
    with tempfile.TemporaryDirectory() as tmpdir:
        jobs = [{"job_id": "zzz"}]
        scrape_path = _write_scrape(tmpdir, jobs)
        scrape_mtime = os.path.getmtime(scrape_path)

        sync_from_scrape(tmpdir, lambda j: (len(j), 0))

        stamp_path = os.path.join(tmpdir, "data", "last_import.txt")
        assert os.path.exists(stamp_path)
        with open(stamp_path) as f:
            assert float(f.read().strip()) == scrape_mtime
