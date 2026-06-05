"""
Vercel serverless entry point.
Imports the Flask app from the project root and exposes it as `app`
for Vercel's Python runtime.
"""
import sys
import os

# Ensure the project root is on sys.path so sibling modules resolve
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Set VERCEL flag before importing app (controls scheduler / scraper behaviour)
os.environ.setdefault("VERCEL", "1")

from app import app  # noqa: E402
from werkzeug.middleware.proxy_fix import ProxyFix

# Vercel terminates TLS and forwards requests over HTTP internally.
# Without this, url_for(..., _external=True) generates http:// URLs,
# causing Google OAuth redirect_uri_mismatch errors.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
