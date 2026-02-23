# Per-Reminder CV Upload — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Each reminder carries its own uploaded CV so job matches are scored against that CV, enabling different users with different backgrounds to get personalised alerts.

**Architecture:** CV text is extracted from the uploaded file (PDF/DOCX/TXT), parsed via the existing `parse_cv_text()`, and stored as `cv_data` inside each reminder's entry in `reminders.json`. At send time (scheduled or manual), jobs are scored on-the-fly using the existing `cv_score(job, cv_data)` and filtered/ranked by CV match % instead of the AI relevance score. Legacy reminders without `cv_data` fall back to `relevance_score` as before.

**Tech Stack:** Flask (file upload via `request.files`), pdfplumber (PDF extraction — already installed), python-docx (DOCX — already installed), existing `parse_cv_text` + `cv_score` from `analyzer.py`, Jinja2 templates, Tailwind CSS via CDN.

---

## Context: Key facts before starting

- `app.py` line 42 already imports `parse_cv_text, cv_score` from `analyzer.py`
- `app.py` lines 1057–1088: existing `/api/cv/upload` uses `pdfplumber` for PDF and `docx` for DOCX — **reuse this exact logic**
- `app.py` lines 1155–1185: current `reminders_create` — needs `enctype` change + file handling + `cv_data` storage
- `app.py` lines 1212–1254: current `reminders_send` — needs CV-based scoring
- `reminder_runner.py` lines 62–98: `run_reminders` loop — needs CV-based scoring
- `database.get_jobs_for_reminder(keyword, min_score, max_jobs)` queries by `relevance_score >= min_score`
- `cv_score(job, cv_data)` in `analyzer.py` takes a job dict and cv_data dict, returns int 0–100, never raises

---

## Task 1: Add `_extract_cv_text()` helper to `app.py`

**Files:**
- Modify: `app.py` (add helper before the `reminders_create` route, around line 1153)

**Step 1:** Read `app.py` lines 1148–1158 to find the exact position just before the `# Reminders` section comment.

**Step 2:** Insert this helper function immediately before the `# Reminders` section comment:

```python
def _extract_cv_text(file_storage) -> str:
    """
    Extract plain text from an uploaded CV file (PDF, DOCX, or TXT).
    Returns extracted text string, or raises ValueError with a user-friendly message.
    """
    filename = (file_storage.filename or "").lower()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    data = file_storage.read()

    if ext == "pdf":
        try:
            import pdfplumber, io
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        except Exception as e:
            raise ValueError(f"PDF parsing failed: {e}")
    elif ext == "docx":
        try:
            import docx, io
            doc = docx.Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            raise ValueError(f"DOCX parsing failed: {e}")
    elif ext in ("txt", ""):
        text = data.decode("utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported file type '.{ext}'. Use PDF, DOCX, or TXT.")

    if not text.strip():
        raise ValueError("Could not extract any text from the CV file.")
    return text
```

**Step 3:** Verify the app still imports cleanly:
```bash
cd /Users/gaurav/job-search-agent && python3 -c "import app; print('OK')"
```
Expected: `OK`

**Step 4:** Commit:
```bash
git add app.py && git commit -m "feat: add _extract_cv_text helper to app.py"
```

---

## Task 2: Modify `reminders_create` to require and store CV

**Files:**
- Modify: `app.py` lines 1155–1185 (`reminders_create` function)

**Step 1:** Read lines 1155–1185 of `app.py` to get the exact current text.

**Step 2:** Replace the entire `reminders_create` function with this new version:

```python
@app.route("/reminders/create", methods=["POST"])
def reminders_create():
    """Create a new reminder with a mandatory CV upload."""
    import uuid
    from reminder_runner import load_reminders, save_reminders
    name = request.form.get("name", "").strip()
    keyword = request.form.get("keyword", "").strip()
    email_addr = request.form.get("email", "").strip()
    if not name or not keyword or not email_addr:
        flash("Name, keyword, and email are required.", "error")
        return redirect(url_for("reminders"))
    try:
        min_score = max(0, min(100, int(request.form.get("min_score", 65))))
        max_jobs = max(1, min(50, int(request.form.get("max_jobs", 20))))
    except (ValueError, TypeError):
        flash("Score and max jobs must be numbers.", "error")
        return redirect(url_for("reminders"))

    cv_file = request.files.get("cv_file")
    if not cv_file or not cv_file.filename:
        flash("A CV/resume file is required to create a reminder.", "error")
        return redirect(url_for("reminders"))
    try:
        cv_text = _extract_cv_text(cv_file)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("reminders"))
    cv_data = parse_cv_text(cv_text)

    all_reminders = load_reminders()
    all_reminders.append({
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "keyword": keyword,
        "min_score": min_score,
        "max_jobs": max_jobs,
        "email": email_addr,
        "enabled": True,
        "last_sent": None,
        "cv_data": cv_data,
    })
    save_reminders(all_reminders)
    flash(f"Reminder '{name}' created with {len(cv_data['skills'])} CV skills detected.", "success")
    return redirect(url_for("reminders"))
```

**Step 3:** Verify:
```bash
cd /Users/gaurav/job-search-agent && python3 -c "
import app
with app.app.test_client() as c:
    r = c.post('/reminders/create', data={'name': 'Test', 'keyword': 'PM', 'email': 'x@x.com'})
    assert r.status_code == 302
    print('OK')
"
```
Expected: `OK` (redirects back to /reminders because no cv_file provided)

**Step 4:** Commit:
```bash
git add app.py && git commit -m "feat: require CV upload when creating reminder"
```

---

## Task 3: Add `POST /reminders/<id>/update-cv` route

**Files:**
- Modify: `app.py` (add new route after `reminders_send`, before the `# Run` section)

**Step 1:** Find the exact text just before `# ---------------------------------------------------------------------------\n# Run` in `app.py`.

**Step 2:** Insert this route immediately before the `# Run` section:

```python
@app.route("/reminders/<reminder_id>/update-cv", methods=["POST"])
def reminders_update_cv(reminder_id):
    """Replace the CV for an existing reminder."""
    from reminder_runner import load_reminders, save_reminders
    cv_file = request.files.get("cv_file")
    if not cv_file or not cv_file.filename:
        flash("Please select a CV file to upload.", "error")
        return redirect(url_for("reminders"))
    try:
        cv_text = _extract_cv_text(cv_file)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("reminders"))
    cv_data = parse_cv_text(cv_text)

    all_reminders = load_reminders()
    updated = False
    for r in all_reminders:
        if r.get("id") == reminder_id:
            r["cv_data"] = cv_data
            updated = True
            break
    if updated:
        save_reminders(all_reminders)
        flash(f"CV updated — {len(cv_data['skills'])} skills detected.", "success")
    else:
        flash("Reminder not found.", "error")
    return redirect(url_for("reminders"))
```

**Step 3:** Verify routes registered:
```bash
cd /Users/gaurav/job-search-agent && python3 -c "
import app
rules = [str(r) for r in app.app.url_map.iter_rules() if 'reminder' in str(r)]
for r in rules: print(r)
"
```
Expected: 6 lines — /reminders, /reminders/create, /reminders/<>/delete, /reminders/<>/toggle, /reminders/<>/send, /reminders/<>/update-cv

**Step 4:** Commit:
```bash
git add app.py && git commit -m "feat: add update-cv route for reminders"
```

---

## Task 4: Add `score_jobs_for_cv_reminder()` helper to `reminder_runner.py`

**Files:**
- Modify: `reminder_runner.py` (add helper function before `run_reminders`)

This function centralises the CV-based scoring logic so both `run_reminders` and the `reminders_send` route use identical behaviour.

**Step 1:** Read `reminder_runner.py` to find the exact line just before `def run_reminders`.

**Step 2:** Insert this function immediately before `def run_reminders`:

```python
def score_jobs_for_cv_reminder(reminder: dict) -> list:
    """
    Fetch and score jobs for a reminder that has cv_data.

    - Fetches up to 200 candidate jobs matching the keyword (ignoring min_score)
    - Scores each against the reminder's CV using cv_score()
    - Filters by min_score, sorts by CV match % descending, returns top max_jobs

    Returns a list of job dicts (same shape as get_jobs_for_reminder returns).
    Falls back to relevance_score query if cv_data is absent.
    """
    from database import get_jobs_for_reminder
    from analyzer import cv_score

    keyword = (reminder.get("keyword") or "").strip()
    min_score = max(0, min(100, int(reminder.get("min_score", 65))))
    max_jobs = max(1, min(50, int(reminder.get("max_jobs", 20))))
    cv_data = reminder.get("cv_data")

    if not cv_data:
        # Legacy fallback: filter by AI relevance score
        return get_jobs_for_reminder(keyword, min_score, max_jobs)

    # Fetch broad candidate set (skip score filter, use large limit)
    candidates = get_jobs_for_reminder(keyword, min_score=0, max_jobs=200)
    if not candidates:
        return []

    # Score each job against this reminder's CV
    scored = [(job, cv_score(job, cv_data)) for job in candidates]

    # Filter by min_score, sort descending, cap at max_jobs
    filtered = sorted(
        [(j, s) for j, s in scored if s >= min_score],
        key=lambda x: -x[1],
    )[:max_jobs]

    return [j for j, _ in filtered]
```

**Step 3:** Verify it imports:
```bash
cd /Users/gaurav/job-search-agent && python3 -c "from reminder_runner import score_jobs_for_cv_reminder; print('OK')"
```
Expected: `OK`

**Step 4:** Commit:
```bash
git add reminder_runner.py && git commit -m "feat: add score_jobs_for_cv_reminder helper"
```

---

## Task 5: Update `run_reminders()` to use CV-based scoring

**Files:**
- Modify: `reminder_runner.py` — the `run_reminders` function

**Step 1:** Read `reminder_runner.py` lines 38–99 to see the current `run_reminders` function.

**Step 2:** Find this block inside `run_reminders`:

```python
        jobs = get_jobs_for_reminder(keyword, min_score, max_jobs)
        if not jobs:
            logger.info("Reminder '%s': no jobs found (keyword=%s, min_score=%d)", name, keyword, min_score)
            continue
```

Replace it with:

```python
        jobs = score_jobs_for_cv_reminder(reminder)
        if not jobs:
            logger.info("Reminder '%s': no jobs found (keyword=%s, min_score=%d)", name, keyword, min_score)
            continue
```

**Step 3:** Remove the now-unused import of `get_jobs_for_reminder` at the top of `run_reminders` (it's now used inside `score_jobs_for_cv_reminder`). Find:

```python
    from database import get_jobs_for_reminder
    from email_notifier import send_job_email
```

Change to:

```python
    from email_notifier import send_job_email
```

**Step 4:** Verify:
```bash
cd /Users/gaurav/job-search-agent && python3 -c "from reminder_runner import run_reminders; print('OK')"
```
Expected: `OK`

**Step 5:** Commit:
```bash
git add reminder_runner.py && git commit -m "feat: use CV-based scoring in run_reminders"
```

---

## Task 6: Update `reminders_send` in `app.py` to use CV-based scoring

**Files:**
- Modify: `app.py` lines 1212–1254 (`reminders_send` function)

**Step 1:** Read lines 1212–1254 of `app.py`.

**Step 2:** Find this block in `reminders_send`:

```python
    jobs = get_jobs_for_reminder(keyword, min_score, max_jobs)
    if not jobs:
        flash(f"No jobs found matching '{keyword}' with score ≥ {min_score}.", "error")
        return redirect(url_for("reminders"))
```

Replace it with:

```python
    from reminder_runner import score_jobs_for_cv_reminder
    jobs = score_jobs_for_cv_reminder(reminder)
    if not jobs:
        flash(f"No jobs found matching '{keyword}' with score ≥ {min_score}.", "error")
        return redirect(url_for("reminders"))
```

**Step 3:** Remove the now-redundant local variable extractions that are no longer used directly (they are now handled inside `score_jobs_for_cv_reminder`). Keep `keyword`, `min_score`, `max_jobs`, `recipient`, `name` — they are still used for the flash message and email. Only the `get_jobs_for_reminder` call line changes.

**Step 4:** Remove the now-unused import at the top of `reminders_send`:

```python
    from database import get_jobs_for_reminder
```

Find that line inside `reminders_send` and delete it.

**Step 5:** Verify:
```bash
cd /Users/gaurav/job-search-agent && python3 -c "
import app
with app.app.test_client() as c:
    r = c.get('/reminders')
    assert r.status_code == 200
    print('OK')
"
```
Expected: `OK`

**Step 6:** Commit:
```bash
git add app.py && git commit -m "feat: use CV-based scoring in reminders_send"
```

---

## Task 7: Update `templates/reminders.html` — CV upload in form + badges on cards

**Files:**
- Modify: `templates/reminders.html`

### 7a — Create form: add `enctype` and CV file input

**Step 1:** Find the `<form>` tag in the create form:
```html
    <form method="POST" action="{{ url_for('reminders_create') }}">
```
Change to:
```html
    <form method="POST" action="{{ url_for('reminders_create') }}" enctype="multipart/form-data">
```

**Step 2:** Find the closing `</div>` of the grid (the one after the max_jobs field), just before the submit button:
```html
      </div>
      <button type="submit"
```
Insert a new grid item for the CV upload **inside** the grid div, as the 6th cell, right before the closing `</div>`:
```html
        <div class="sm:col-span-2 lg:col-span-3">
          <label class="block text-xs font-medium text-gray-600 mb-1">Your CV / Resume <span class="text-red-500">*</span></label>
          <input type="file" name="cv_file" accept=".pdf,.docx,.txt" required
                 class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 py-2 px-3 file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:text-xs file:font-medium file:bg-indigo-50 file:text-indigo-700 hover:file:bg-indigo-100">
          <p class="text-xs text-gray-400 mt-1">PDF, DOCX, or TXT — used to score job matches for this alert.</p>
        </div>
```

### 7b — Reminder cards: CV badge and Update CV button

**Step 1:** Find the info section in each card, specifically after the last_sent span:
```html
          <span>Last sent: <strong class="text-gray-700">
            {% if r.last_sent %}{{ r.last_sent[:16].replace('T', ' ') }}{% else %}Never{% endif %}
          </strong></span>
        </div>
```

Add a CV badge line immediately after that closing `</div>`:
```html
        <div class="mt-1.5">
          {% if r.cv_data %}
          <span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 font-medium">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
            CV uploaded · {{ r.cv_data.skills | length }} skills
          </span>
          {% else %}
          <span class="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 font-medium">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            No CV — using relevance score
          </span>
          {% endif %}
        </div>
```

**Step 2:** Find the Actions section with the Send Now / Pause / Delete buttons:
```html
      <!-- Actions -->
      <div class="flex items-center gap-2 flex-shrink-0">
        <form method="POST" action="{{ url_for('reminders_send', reminder_id=r.id) }}">
```

Add the Update CV form as the **first** item inside the actions div, before the Send Now form:
```html
      <!-- Actions -->
      <div class="flex items-center gap-2 flex-shrink-0 flex-wrap">
        <form method="POST" action="{{ url_for('reminders_update_cv', reminder_id=r.id) }}" enctype="multipart/form-data" class="flex items-center gap-1">
          <label class="text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 font-medium transition-colors cursor-pointer">
            {% if r.cv_data %}Update CV{% else %}Upload CV{% endif %}
            <input type="file" name="cv_file" accept=".pdf,.docx,.txt" class="hidden" onchange="this.form.submit()">
          </label>
        </form>
```

Note: `onchange="this.form.submit()"` auto-submits when the user picks a file — no extra button needed.

### 7c — Verify

```bash
cd /Users/gaurav/job-search-agent && python3 -c "
import app
with app.app.test_client() as c:
    r = c.get('/reminders')
    assert r.status_code == 200
    assert b'cv_file' in r.data
    assert b'update-cv' in r.data or b'update_cv' in r.data
    print('OK')
"
```
Expected: `OK`

**Step 3:** Commit:
```bash
git add templates/reminders.html && git commit -m "feat: add CV upload to reminder create form and update-cv button to cards"
```

---

## Final Verification Checklist

Start the app and verify:
```bash
cd /Users/gaurav/job-search-agent && python3 app.py
```

- [ ] `http://localhost:5001/reminders` loads with no errors
- [ ] Create form has a "Your CV / Resume *" file input
- [ ] Submitting the create form without a CV file shows error flash "A CV/resume file is required"
- [ ] Submitting with a valid PDF/TXT creates the reminder with "X CV skills detected" in success flash
- [ ] Reminder card shows "CV uploaded · N skills" badge in indigo
- [ ] Legacy reminder (no cv_data) shows "No CV — using relevance score" badge in amber
- [ ] "Update CV" / "Upload CV" button auto-submits when file is selected, updates skill count
- [ ] 6 routes registered: /reminders, /reminders/create, /reminders/<>/delete, /reminders/<>/toggle, /reminders/<>/send, /reminders/<>/update-cv
