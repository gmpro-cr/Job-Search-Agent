"""
blob_storage.py - Tiny adapter over Vercel Blob with a local-filesystem
fallback for dev.

Public API:
    put(pathname, content, content_type=None) -> str (public URL or local path)
    get(pathname) -> bytes | None
    list(prefix="") -> list[dict]   each dict has at least pathname, size, uploaded_at, url
    delete(pathname) -> None
    url(pathname) -> str  (public URL)
    exists(pathname) -> bool

Vercel Blob is used when BLOB_READ_WRITE_TOKEN is set in the environment.
Otherwise we fall back to <DATA_DIR>/blob_local/<pathname> so dev keeps
working without Vercel credentials.

Notes on the Vercel API (as of 2026-02):
    PUT  https://blob.vercel-storage.com/<pathname>?addRandomSuffix=0
    GET  <public URL returned by PUT or url(pathname)>
    DELETE  https://blob.vercel-storage.com/delete  (POST, body: {urls:[...]})
    LIST   https://api.vercel.com/v9/blob/list?prefix=...

We disable addRandomSuffix so that overwriting the same pathname is
idempotent — important for "digests/2026-05-14.html" and similar fixed
names. We also disable token caching so a token rotation is picked up
immediately on the next call.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Iterable

logger = logging.getLogger(__name__)

# Surface DATABASE_URL / BLOB token from .env when imported standalone.
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:
    pass

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.environ.get("DATA_DIR", _BASE_DIR)
_LOCAL_ROOT = os.path.join(_DATA_DIR, "blob_local")

_BLOB_API_BASE = "https://blob.vercel-storage.com"
_VERCEL_API_BASE = "https://api.vercel.com"


def _token() -> str | None:
    t = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip().strip('"')
    return t or None


def _use_blob() -> bool:
    return bool(_token())


def _public_base() -> str | None:
    """
    Public base URL for the store. Vercel's read-write token encodes the
    store id in its prefix:  vercel_blob_rw_<storeId>_<...>
    The public URL pattern is <storeId-lowercase>.public.blob.vercel-storage.com.
    """
    t = _token()
    if not t:
        return None
    parts = t.split("_")
    # Expected layout: ['vercel', 'blob', 'rw', '<storeId>', '<random>']
    if len(parts) >= 5 and parts[0] == "vercel" and parts[1] == "blob":
        store_id = parts[3]
        return f"https://{store_id.lower()}.public.blob.vercel-storage.com"
    return None


def _local_path(pathname: str) -> str:
    safe = pathname.lstrip("/").replace("..", "_")
    return os.path.join(_LOCAL_ROOT, safe)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def put(pathname: str, content, content_type: str | None = None) -> str:
    """Upload bytes/str/file-like to Blob (or local fallback). Returns URL."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    elif hasattr(content, "read"):
        content = content.read()

    if _use_blob():
        return _blob_put(pathname, content, content_type)

    fp = _local_path(pathname)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "wb") as f:
        f.write(content)
    return f"file://{fp}"


def get(pathname: str) -> bytes | None:
    """Fetch raw bytes for the given pathname. Returns None when missing."""
    if _use_blob():
        return _blob_get(pathname)

    fp = _local_path(pathname)
    if not os.path.exists(fp):
        return None
    with open(fp, "rb") as f:
        return f.read()


def list(prefix: str = "") -> list[dict]:
    """List blobs whose pathname starts with `prefix`. Each entry has
    pathname / size / uploaded_at / url."""
    if _use_blob():
        return _blob_list(prefix)

    out: list[dict] = []
    root = _LOCAL_ROOT
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if prefix and not rel.startswith(prefix):
                continue
            stat = os.stat(full)
            out.append({
                "pathname": rel,
                "size": stat.st_size,
                "uploaded_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "url": f"file://{full}",
            })
    out.sort(key=lambda d: d["uploaded_at"], reverse=True)
    return out


def delete(pathnames) -> None:
    """Delete one or more blobs. Accepts a single pathname or an iterable."""
    if isinstance(pathnames, str):
        pathnames = [pathnames]
    pathnames = [p for p in pathnames if p]
    if not pathnames:
        return
    if _use_blob():
        _blob_delete(pathnames)
        return
    for p in pathnames:
        fp = _local_path(p)
        if os.path.exists(fp):
            os.remove(fp)


def url(pathname: str) -> str:
    """Return the public URL for a blob (or a file:// URI in local mode)."""
    base = _public_base()
    if base:
        return f"{base}/{pathname.lstrip('/')}"
    return f"file://{_local_path(pathname)}"


def exists(pathname: str) -> bool:
    if _use_blob():
        return _blob_get(pathname, head_only=True) is not None
    return os.path.exists(_local_path(pathname))


# ---------------------------------------------------------------------------
# Vercel Blob REST client
# ---------------------------------------------------------------------------

def _blob_put(pathname: str, content: bytes, content_type: str | None) -> str:
    import requests
    headers = {
        "Authorization": f"Bearer {_token()}",
        "x-content-type": content_type or "application/octet-stream",
        # x-add-random-suffix=0 keeps pathnames idempotent; overwriting same
        # path replaces the blob.
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
    }
    if content_type:
        headers["Content-Type"] = content_type
    safe_path = pathname.lstrip("/")
    r = requests.put(
        f"{_BLOB_API_BASE}/{safe_path}",
        headers=headers,
        data=content,
        timeout=30,
    )
    if r.status_code >= 400:
        logger.error("Blob PUT failed (%s): %s", r.status_code, r.text[:300])
        r.raise_for_status()
    try:
        data = r.json()
        return data.get("url") or url(pathname)
    except ValueError:
        return url(pathname)


def _blob_get(pathname: str, head_only: bool = False) -> bytes | None:
    import requests
    public = url(pathname)
    method = requests.head if head_only else requests.get
    r = method(public, timeout=30)
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        logger.warning("Blob GET %s -> %s", pathname, r.status_code)
        return None
    return b"" if head_only else r.content


def _blob_list(prefix: str) -> list[dict]:
    import requests
    headers = {"Authorization": f"Bearer {_token()}"}
    params: dict = {"limit": 1000}
    if prefix:
        params["prefix"] = prefix
    out: list[dict] = []
    cursor = None
    while True:
        if cursor:
            params["cursor"] = cursor
        r = requests.get(
            f"{_BLOB_API_BASE}/",
            headers=headers,
            params=params,
            timeout=30,
        )
        if r.status_code >= 400:
            logger.error("Blob LIST failed (%s): %s", r.status_code, r.text[:300])
            return out
        data = r.json() or {}
        for b in data.get("blobs", []):
            out.append({
                "pathname": b.get("pathname") or "",
                "size": b.get("size", 0),
                "uploaded_at": b.get("uploadedAt") or "",
                "url": b.get("url") or url(b.get("pathname") or ""),
            })
        cursor = data.get("cursor")
        if not data.get("hasMore"):
            break
    return out


def _blob_delete(pathnames: Iterable[str]) -> None:
    import requests
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }
    urls = [url(p) for p in pathnames]
    # Vercel Blob REST API: POST /delete  body: {urls:[...]}
    r = requests.post(
        f"{_BLOB_API_BASE}/delete",
        headers=headers,
        data=json.dumps({"urls": urls}),
        timeout=30,
    )
    if r.status_code >= 400:
        logger.warning("Blob DELETE failed (%s): %s", r.status_code, r.text[:300])
