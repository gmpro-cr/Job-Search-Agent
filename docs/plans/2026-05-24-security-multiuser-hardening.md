# Security & Multi-User Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove OWNER_EMAIL single-owner bypass, fix all critical/high/important security and performance issues, and make the app safe for public use where any user authenticates via Google OAuth and sees only their own data.

**Architecture:** All auth flows through Google OAuth (`/auth/google` → `/auth/callback`). `before_request` enforces login for every route except the auth endpoints. Per-user isolation already exists via `user_job_state`, `user_preferences`, `user_cv_data` tables — this plan removes the bypass layer and hardens the perimeter. No new tables needed.

**Tech Stack:** Flask 3, flask-wtf (CSRF), authlib (OAuth), psycopg/SQLite dual-driver, Vercel/local dual-deploy.

---

## Task 1: Remove OWNER_EMAIL auto-login & harden session config

**Files:**
- Modify: `app.py:60-115`

**Step 1: Replace `before_request` and secret-key setup**

In `app.py`, replace lines 60–115 with:

```python
# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

_IS_VERCEL = bool(os.environ.get("VERCEL"))

app = Flask(__name__)

_secret = os.environ.get("FLASK_SECRET")
if not _secret:
    if _IS_VERCEL:
        raise RuntimeError("FLASK_SECRET env var is required in production. "
                           "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(48))'")
    # Local dev: use a stable but non-secret fallback
    _secret = "dev-only-insecure-key-do-not-use-in-production"
app.secret_key = _secret

app.config["SESSION_COOKIE_SECURE"]   = _IS_VERCEL
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
```

**Step 2: Replace `require_login` before_request**

Remove the `OWNER_EMAIL` auto-login block. The new `before_request` is:

```python
_PUBLIC_ENDPOINTS = frozenset({
    'login', 'auth_google', 'auth_callback', 'static',
    'approve_outreach', 'skip_outreach',   # token-gated, no session needed
})

@app.before_request
def require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS:
        return
    if session.get('user') is None:
        if request.is_json or request.path.startswith('/api/'):
            from flask import abort
            abort(401)
        return redirect(url_for('login', next=request.path))
```

Note: `request.path` (not `request.url`) prevents leaking the full URL including query params into the redirect parameter.

**Step 3: Remove `OWNER_EMAIL` references from `login`, `auth_callback`, `auth_dev_login`, `logout`, `index`**

```python
@app.route('/login')
def login():
    if session.get('user'):
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/auth/google')
def auth_google():
    if not os.environ.get("GOOGLE_CLIENT_ID"):
        flash("Google OAuth is not configured.", "error")
        return redirect(url_for('login'))
    redirect_uri = url_for('auth_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/callback')
def auth_callback():
    try:
        token = google.authorize_access_token()
        userinfo = token.get('userinfo') or {}
        email = (userinfo.get('email') or '').strip().lower()
        if not email:
            flash("Authentication failed: no email returned.", "error")
            return redirect(url_for('login'))
        uid = get_or_create_user(email, userinfo.get('name', ''), userinfo.get('picture', ''))
        session.permanent = True
        session['user'] = {
            'email': email,
            'name':  userinfo.get('name', ''),
            'picture': userinfo.get('picture', ''),
            'id':    uid,
        }
    except Exception as e:
        logger.error("OAuth callback error: %s", e)
        flash("Authentication failed.", "error")
        return redirect(url_for('login'))
    next_url = request.args.get('next') or url_for('dashboard')
    # Reject external redirects (open-redirect fix)
    from urllib.parse import urlparse
    if urlparse(next_url).netloc:
        next_url = url_for('dashboard')
    return redirect(next_url)

@app.route('/auth/dev-login', methods=['POST'])
def auth_dev_login():
    """Local dev only — not available when running on Vercel."""
    if _IS_VERCEL:
        abort(403)
    uid = get_or_create_user('dev@localhost', 'Dev User')
    session.permanent = True
    session['user'] = {'email': 'dev@localhost', 'name': 'Dev User', 'picture': '', 'id': uid}
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/")
def index():
    if session.get('user'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))
```

**Step 4: Fix remaining `OWNER_EMAIL` references throughout app.py**

Search and fix:
```bash
grep -n "OWNER_EMAIL\|get_owner_id\|owner_locked" app.py
```

For each `current_user_id() or get_owner_id()` pattern (lines 2626, 2669), replace with just `current_user_id()`. Remove the `get_owner_id` import/call.

For `owner_locked=False` in `render_template('login.html', ...)` — update the login template to not reference that variable.

**Step 5: Verify locally**

```bash
python app.py
# Visit http://localhost:5001 — should redirect to /login, not auto-login
# Click Google login (or use dev-login)
```

**Step 6: Commit**
```bash
git add app.py templates/login.html
git commit -m "security: remove OWNER_EMAIL auto-login, harden session config, fix open redirect"
```

---

## Task 2: Add CSRF protection

**Files:**
- Modify: `requirements.txt`
- Modify: `app.py` (after Flask app creation)
- Modify: `templates/base.html` (add meta tag for CSRF token)

**Step 1: Add flask-wtf to requirements**

In `requirements.txt`, add after `flask>=3.0.0`:
```
flask-wtf>=1.2.0
```

**Step 2: Enable CSRFProtect in app.py**

After `app = Flask(__name__)` and config setup, add:
```python
from flask_wtf.csrf import CSRFProtect, generate_csrf
csrf = CSRFProtect(app)

# Exempt token-gated endpoints that have their own auth
csrf.exempt('approve_outreach')
csrf.exempt('skip_outreach')

@app.after_request
def set_csrf_cookie(response):
    # Make token available to JS via meta tag and cookie
    response.headers['X-CSRF-Token'] = generate_csrf()
    return response
```

**Step 3: Add CSRF meta tag to base.html**

In `templates/base.html`, inside `<head>`, add:
```html
<meta name="csrf-token" content="{{ csrf_token() }}">
```

**Step 4: Add CSRF token to all HTML forms**

Every `<form method="POST">` in every template needs:
```html
<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
```

Templates to update (grep to confirm):
```bash
grep -rl "method=\"POST\"\|method='POST'" templates/
```

Expected: `cv.html`, `preferences.html`, `settings.html`, `login.html`, `base.html` (any inline forms), `dashboard.html`.

**Step 5: Add CSRF header to all JS fetch() calls**

In `templates/base.html`, add a global fetch interceptor in the `<head>` script block:
```html
<script>
(function() {
  const _csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const _origFetch = window.fetch;
  window.fetch = function(url, opts) {
    opts = opts || {};
    if (opts.method && opts.method.toUpperCase() !== 'GET') {
      opts.headers = Object.assign({'X-CSRFToken': _csrfToken}, opts.headers || {});
    }
    return _origFetch(url, opts);
  };
})();
</script>
```

**Step 6: Mark API routes that intentionally accept external POSTs as CSRF-exempt**

In `app.py`, add `@csrf.exempt` decorator to:
- `api_import_jobs` (called by GitHub Actions scraper with `IMPORT_SECRET`)
- `approve_outreach` / `skip_outreach` (token-gated email links, no session)
- `auth_callback` (OAuth redirect from Google, no session yet)
- `auth_dev_login` (dev-only)

Example:
```python
@app.route("/api/import-jobs", methods=["POST"])
@csrf.exempt
def api_import_jobs():
    ...
```

**Step 7: Test locally**

```bash
python app.py
# Log in, try submitting the CV upload form — should succeed
# Try a curl POST without CSRF token — should get 400
curl -X POST http://localhost:5001/cv -d "cv_file=test"
# Expected: 400 Bad Request (CSRF validation failed)
```

**Step 8: Commit**
```bash
git add requirements.txt app.py templates/
git commit -m "security: add CSRF protection via flask-wtf to all state-mutating routes"
```

---

## Task 3: Add `require_user_id()` to all destructive endpoints

**Files:**
- Modify: `app.py` (multiple routes)

**Step 1: Find all routes missing explicit auth check**

```bash
grep -n "^@app.route" app.py | grep -v "GET\|methods=\[.GET.\]" | head -60
```

Add `uid = require_user_id()` as the first line of each of these handlers:
- `api_dedup` (~line 1851)
- `api_portals_update` (~line 2010)
- `api_scraper_start` (~line 2036)
- `api_agent_run` (~line 3095)
- `pipeline_open_terminal` (~line 3648)
- `api_prd_send_now` (~line 3838)
- `api_digest_deactivate` — already has it; verify
- `api_digest_archive_delete` — already has it; verify
- `api_hiring_managers_search` — already has it; verify

Also add a guard to `pipeline_open_terminal` to block on Vercel:
```python
@app.route("/api/pipeline/open-terminal", methods=["POST"])
def pipeline_open_terminal():
    uid = require_user_id()
    if _IS_VERCEL:
        return jsonify({"ok": False, "error": "Not available on Vercel"}), 400
    ...
```

**Step 2: Commit**
```bash
git add app.py
git commit -m "security: add require_user_id() to all destructive/admin endpoints"
```

---

## Task 4: Fix open redirect in CV upload `next` parameter

**Files:**
- Modify: `app.py:2506`

**Step 1: Replace unsafe redirect**

Find:
```python
return redirect(request.form.get("next") or url_for("cv_page"))
```

Replace with:
```python
_next = request.form.get("next", "")
from urllib.parse import urlparse
if not _next or urlparse(_next).netloc:
    _next = url_for("cv_page")
return redirect(_next)
```

Also fix the `before_request` redirect to only pass the path (already done in Task 1: using `request.path` not `request.url`).

**Step 2: Commit**
```bash
git add app.py
git commit -m "security: fix open redirect in CV upload next parameter"
```

---

## Task 5: Harden import endpoint and approval token race condition

**Files:**
- Modify: `app.py` (import endpoint, approve_outreach)
- Modify: `database.py` (atomic approval update)

**Step 1: Move secret check before body parse in import endpoint**

Find `api_import_jobs` (around line 1957). Move the secret validation to use a request header instead of body:

```python
@app.route("/api/import-jobs", methods=["POST"])
@csrf.exempt
def api_import_jobs():
    # Auth: check header first, before touching body
    import_secret = os.environ.get("IMPORT_SECRET", "")
    provided = request.headers.get("X-Import-Secret", "") or (request.get_json(silent=True, force=True) or {}).get("secret", "")
    if import_secret and provided != import_secret:
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    ...
```

**Step 2: Fix approval token race condition in database.py**

Replace `get_outreach_by_token` + separate `update_outreach_status` with an atomic fetch-and-claim:

```python
def claim_outreach_token(token: str) -> dict | None:
    """
    Atomically claim a pending approval token.
    Returns the outreach row if the claim succeeded (status was 'pending'),
    None if already claimed or token not found.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute(
        """UPDATE outreach_queue
              SET status = 'sent', sent_at = ?
            WHERE approval_token = ? AND status = 'pending'""",
        (now, token),
    )
    conn.commit()
    if cursor.rowcount == 0:
        conn.close()
        return None
    cursor.execute("SELECT * FROM outreach_queue WHERE approval_token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None
```

**Step 3: Update `approve_outreach` in app.py to use `claim_outreach_token`**

```python
@app.route("/api/approve/<token>")
def approve_outreach(token):
    from database import claim_outreach_token
    item = claim_outreach_token(token)
    if item is None:
        return "This approval link has already been used or has expired.", 200
    # proceed with sending email using item["hm_email"] etc.
    ...
```

**Step 4: Add `created_at` expiry check (48h)**

In `claim_outreach_token`, add before the UPDATE:
```python
cursor.execute(
    "SELECT created_at FROM outreach_queue WHERE approval_token = ? AND status = 'pending'",
    (token,)
)
row = cursor.fetchone()
if not row:
    conn.close()
    return None
from datetime import datetime, timedelta
try:
    age = datetime.now() - datetime.fromisoformat(row["created_at"])
    if age > timedelta(hours=48):
        conn.close()
        return None
except Exception:
    pass
```

**Step 5: Commit**
```bash
git add app.py database.py
git commit -m "security: atomic approval token claim, 48h expiry, header-first import secret"
```

---

## Task 6: Fix `bulk_set_user_cv_scores` — replace double-query loop with UPSERT executemany

**Files:**
- Modify: `database.py:1715-1737`

**Step 1: Replace the implementation**

```python
def bulk_set_user_cv_scores(user_id: int, scores: dict) -> int:
    """scores: {job_id: cv_score_int}. Returns number of rows written."""
    if not user_id or not scores:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    rows = [
        (user_id, job_id, int(score or 0), now, int(score or 0), now)
        for job_id, score in scores.items()
    ]
    cursor.executemany(
        """INSERT INTO user_job_state (user_id, job_id, cv_score, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (user_id, job_id)
           DO UPDATE SET cv_score = ?, updated_at = ?""",
        rows,
    )
    written = cursor.rowcount if cursor.rowcount >= 0 else len(rows)
    conn.commit()
    conn.close()
    return written
```

Note: SQLite `executemany` returns `rowcount = -1` for upserts; use `len(rows)` as fallback.

**Step 2: Verify existing callers still work**

```bash
grep -n "bulk_set_user_cv_scores" app.py database.py
```

No signature change — just faster.

**Step 3: Commit**
```bash
git add database.py
git commit -m "perf: bulk_set_user_cv_scores uses single UPSERT executemany instead of 2N queries"
```

---

## Task 7: Fix `find_similar_job` — push company filter to SQL

**Files:**
- Modify: `database.py:824-840`

**Step 1: Replace implementation**

```python
def find_similar_job(company, role, location):
    """Check if a fuzzy-similar job already exists. Returns job_id or None."""
    from scrapers import _normalize_company_name, _fuzzy_role_match
    norm_company = _normalize_company_name(company)
    conn = get_connection()
    cursor = conn.cursor()
    # Filter by normalised company name in SQL to avoid full-table Python scan
    cursor.execute(
        """SELECT job_id, company, role
             FROM job_listings
            WHERE LOWER(TRIM(company)) = ?
            ORDER BY date_found DESC
            LIMIT 50""",
        (norm_company.lower(),),
    )
    rows = cursor.fetchall()
    conn.close()
    for r in rows:
        if _fuzzy_role_match(role, r["role"]):
            return r["job_id"]
    return None
```

This reduces Python-side comparisons from 2,000 to ≤50 for any given company.

**Step 2: Commit**
```bash
git add database.py
git commit -m "perf: find_similar_job filters by company in SQL, avoids 2000-row Python scan"
```

---

## Task 8: Move CV rescoring off the request thread

**Files:**
- Modify: `app.py` (cv_page POST handler ~line 2504)

**Step 1: Wrap `_rescore_all_jobs` in a background thread on CV upload**

```python
import threading as _threading

def _rescore_in_background(cv_data, preferences, user_id):
    try:
        _rescore_all_jobs(cv_data, preferences=preferences, user_id=user_id)
    except Exception as e:
        logger.error("Background rescore failed for user %s: %s", user_id, e)

# Inside cv_page POST, replace the blocking call:
# OLD: _rescore_all_jobs(cv_data, preferences=merged_prefs)
# NEW:
uid = current_user_id()
t = _threading.Thread(
    target=_rescore_in_background,
    args=(cv_data, merged_prefs, uid),
    daemon=True,
)
t.start()
flash(f"CV uploaded — {len(cv_data['skills'])} skills detected. Scoring jobs in the background…", "success")
```

Do the same for the preferences POST handler where `_rescore_all_jobs` is called.

**Step 2: Commit**
```bash
git add app.py
git commit -m "perf: CV rescoring runs in background thread, no longer blocks HTTP response"
```

---

## Task 9: Fix file handle leaks — use context managers

**Files:**
- Modify: `app.py` (~lines 454, 588, 2182, 2239)

**Step 1: Replace bare open() calls**

```python
# BAD (lines 454, 588):
open(prd_sent_flag, "w").close()
# GOOD:
from pathlib import Path
Path(prd_sent_flag).touch()

# BAD (lines 2182, 2239):
data = _json.loads(open(path, encoding="utf-8").read())
# GOOD:
with open(path, encoding="utf-8") as _f:
    data = _json.load(_f)

# BAD (api_hiring_managers_search):
open(fallback, "w", encoding="utf-8").write(_json.dumps(existing, indent=2))
# GOOD:
with open(fallback, "w", encoding="utf-8") as _f:
    _json.dump(existing, _f, indent=2)
```

**Step 2: Commit**
```bash
git add app.py
git commit -m "fix: replace bare open() calls with context managers to prevent file handle leaks"
```

---

## Task 10: Fix reminder ID entropy and add `date_str` validation

**Files:**
- Modify: `app.py` (~lines 1288, 3208, 3252, 3826)

**Step 1: Use full UUID for reminder IDs**

Find all:
```python
"id": uuid.uuid4().hex[:8],
```
Replace with:
```python
"id": uuid.uuid4().hex,   # 128-bit, no truncation
```

**Step 2: Add `date_str` validation in PRD route**

```python
@app.route("/api/prd/<date_str>")
def api_prd_for_date(date_str):
    import re
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date_str):
        abort(400)
    ...
```

**Step 3: Commit**
```bash
git add app.py
git commit -m "security: full 128-bit reminder IDs, validate date_str path parameter"
```

---

## Task 11: Move hiring_managers.py into the repo

**Files:**
- Create: `hiring_managers_search.py` (copy from `~/Documents/Claude/hiring_managers.py`)
- Modify: `app.py` (replace `_load_hm()` calls)

**Step 1: Copy the file**

```bash
cp ~/Documents/Claude/hiring_managers.py /Users/gaurav/job-search-agent/hiring_managers_search.py
```

**Step 2: Update `_load_hm()` and all its callers in app.py**

Remove `_load_hm()` function entirely. Replace all `hm = _load_hm()` with:
```python
import hiring_managers_search as hm_mod
```

And update callers:
```python
# Before: hm = _load_hm(); hm.get_new_hiring_managers(...)
# After:
from hiring_managers_search import get_new_hiring_managers, load_hr_sent, update_hr_sent, load_all_contacts

contacts = get_new_hiring_managers(sent, role_keywords=..., skills=..., location=..., target=10)
```

**Step 3: Update `_load_all_hm_contacts` in app.py to use the module directly**

```python
def _load_all_hm_contacts() -> list:
    from hiring_managers_search import load_all_contacts
    return load_all_contacts()
```

**Step 4: Add `ddgs` to requirements-scraper.txt** (it's only needed when running the search)

```bash
grep -q "ddgs" requirements-scraper.txt || echo "ddgs>=6.0" >> requirements-scraper.txt
```

**Step 5: Commit**
```bash
git add hiring_managers_search.py app.py requirements-scraper.txt
git commit -m "refactor: move hiring_managers.py into repo as hiring_managers_search.py"
```

---

## Task 12: Fix outbox unbounded fetch + N+1 on CV page

**Files:**
- Modify: `database.py` (`get_outreach_queue`)
- Modify: `app.py` (outbox route, cv_page skill-count queries)

**Step 1: Add LIMIT to `get_outreach_queue`**

```python
def get_outreach_queue(status: str, limit: int = 100, offset: int = 0) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM outreach_queue WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (status, limit, offset),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_outreach_queue_count(status: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as c FROM outreach_queue WHERE status = ?", (status,))
    count = cursor.fetchone()["c"]
    conn.close()
    return count
```

**Step 2: Update outbox route to use counts + paginated fetch**

```python
@app.route("/outbox")
def outbox():
    counts = {
        s: get_outreach_queue_count(s)
        for s in ("pending", "sent", "skipped")
    }
    tab = request.args.get("tab", "pending")
    page = max(1, int(request.args.get("page", 1)))
    items = get_outreach_queue(tab, limit=50, offset=(page - 1) * 50)
    return render_template("outbox.html", items=items, counts=counts, tab=tab, page=page)
```

**Step 3: Replace N+1 skill-count queries on CV page**

In `cv_page` GET handler, replace the loop of individual LIKE queries with a single aggregated query or in-Python count after one fetch:

```python
# Instead of N separate LIKE queries per skill:
# Get all job descriptions once, count skill occurrences in Python
if skills:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT job_description FROM job_listings LIMIT 2000")
    all_jds = " ".join((r["job_description"] or "") for r in cur.fetchall()).lower()
    conn.close()
    skill_counts = {s: all_jds.count(s.lower()) for s in skills}
```

**Step 4: Commit**
```bash
git add app.py database.py
git commit -m "perf: paginate outbox fetch, replace N+1 skill-count queries with single pass"
```

---

## Task 13: Update login.html to remove owner_locked references, add Google OAuth button

**Files:**
- Modify: `templates/login.html`

**Step 1: Ensure login.html works without `owner_locked` variable**

The template should show Google OAuth button unconditionally (when `GOOGLE_CLIENT_ID` is set) and a dev-login form only when not on Vercel. Remove all `owner_locked` conditionals.

```html
{% if config.get('GOOGLE_CLIENT_ID') or true %}
<a href="{{ url_for('auth_google') }}" class="btn btn-default" style="...">
  Sign in with Google
</a>
{% endif %}

{% if not is_vercel %}
<form method="POST" action="{{ url_for('auth_dev_login') }}">
  <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
  <button type="submit" class="btn btn-outline">Dev login (local only)</button>
</form>
{% endif %}
```

Pass `is_vercel=_IS_VERCEL` from the login route.

**Step 2: Commit**
```bash
git add templates/login.html app.py
git commit -m "ui: clean up login page, remove owner_locked, show dev-login only in local mode"
```

---

## Task 14: Final integration test + deploy

**Step 1: Run locally end-to-end**

```bash
python app.py
```

Checklist:
- [ ] `/` redirects to `/login` (no auto-login)
- [ ] Google OAuth flow completes and lands on `/dashboard`
- [ ] Dev login works at `http://localhost:5001`
- [ ] CV upload scores jobs and shows on jobs page
- [ ] POST without CSRF token returns 400
- [ ] All API endpoints return 401 when not logged in

**Step 2: Deploy to Vercel**

```bash
git push origin main
# Vercel auto-deploys from main
```

**Step 3: Verify FLASK_SECRET is set in Vercel**

```bash
vercel env ls | grep FLASK_SECRET
# If missing:
python -c 'import secrets; print(secrets.token_urlsafe(48))'
# Copy output, then:
echo "<value>" | vercel env add FLASK_SECRET production
```

**Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "chore: post-deploy fixes from integration testing"
git push origin main
```

---

## Summary

| Task | Issue Fixed | Severity |
|------|-------------|----------|
| 1 | Remove OWNER_EMAIL auto-login, session security flags, startup secret check | C2, C3, I6 |
| 2 | CSRF protection on all state-mutating routes | C1 |
| 3 | `require_user_id()` on destructive endpoints | H1 |
| 4 | Open redirect in `next` parameter | H2 |
| 5 | Import secret in header, atomic approval token | H5, H6 |
| 6 | `bulk_set_user_cv_scores` UPSERT executemany | H4, S1 |
| 7 | `find_similar_job` SQL filter | I2 |
| 8 | CV rescoring in background thread | H4 |
| 9 | File handle leaks | S3, S4 |
| 10 | Full reminder IDs, `date_str` validation | I5, S5 |
| 11 | Move `hiring_managers.py` into repo | I4 |
| 12 | Outbox pagination, N+1 on CV page | I1, I3 |
| 13 | Login page cleanup | C3 |
| 14 | Integration test + deploy | — |
