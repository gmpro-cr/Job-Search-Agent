"""
Upload an encrypted Neon dump to Vercel Blob and prune old ones.

Run by .github/workflows/backup.yml after `pg_dump | gzip | gpg`. The Blob
store has public access, so only ever pass it the .gpg file — never a plain
dump. Keeps the newest KEEP backups under backups/.

Neon's free plan only keeps 6 hours of history, so these dumps are the only
way back from a bad write noticed later than that (see tests/conftest.py).

Usage:
    python -m scripts.backup_db neon.sql.gz.gpg
"""
import sys
from datetime import datetime, timezone

import blob_storage

PREFIX = "backups/"
KEEP = 14


def main(path: str) -> None:
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith((b"\x8c", b"\xc3")):  # OpenPGP symmetric-key packet tags
        raise SystemExit(f"{path} does not look gpg-encrypted; refusing to upload to public Blob")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pathname = f"{PREFIX}neon_{stamp}.sql.gz.gpg"
    blob_storage.put(pathname, data, "application/octet-stream")
    print(f"uploaded {pathname} ({len(data) / 1e6:.1f} MB)")

    old = sorted(blob_storage.list(PREFIX), key=lambda b: b["pathname"], reverse=True)[KEEP:]
    if old:
        blob_storage.delete([b["pathname"] for b in old])
        print(f"pruned {len(old)} old backups")


if __name__ == "__main__":
    main(sys.argv[1])
