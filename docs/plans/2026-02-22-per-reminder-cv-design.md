# Per-Reminder CV Upload — Design Document

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Each reminder carries its own CV so job match scores are personalised per user/background.

**Date:** 2026-02-22

---

## Scope

- CV upload is **mandatory** when creating a reminder
- Each reminder stores parsed CV text inline in `reminders.json`
- When a reminder has a CV, `min_score` filters by **CV match %** (not AI relevance score)
- Jobs are scored on-the-fly using existing `cv_score(job, cv_data)` from `analyzer.py`
- A "Update CV" button lets users replace the CV without recreating the reminder
- Legacy reminders without a CV fall back to `relevance_score` as before

---

## Data Model Change

### `reminders.json` — new `cv_data` field per reminder

```json
{
  "id": "abc12345",
  "name": "PM jobs Bangalore",
  "keyword": "Product Manager",
  "min_score": 65,
  "max_jobs": 20,
  "email": "user@gmail.com",
  "enabled": true,
  "last_sent": null,
  "cv_data": {
    "skills": ["Product Strategy", "Roadmap", "Agile"],
    "raw_text": "John Doe\nSenior PM with 6 years...",
    "uploaded_at": "2026-02-22T10:00:00"
  }
}
```

`cv_data` is `null` / absent for legacy reminders (handled gracefully).

---

## Architecture

### CV extraction

Supported upload formats: `.pdf` and `.txt`.

- **PDF:** Use `pdfminer.six` (already installed as a dependency) — `extract_text(BytesIO(file_bytes))` from `pdfminer.high_level`
- **TXT:** Decode as UTF-8

After extracting raw text, call `parse_cv_text(text)` from `analyzer.py` to get `{skills, raw_text, uploaded_at}`.

### New Flask routes in `app.py`

| Method | Route | Action |
|--------|-------|--------|
| POST | `/reminders/create` | **Modified** — now accepts `multipart/form-data`, extracts CV from file upload, stores `cv_data` |
| POST | `/reminders/<id>/update-cv` | **New** — re-upload CV for an existing reminder |

### Scoring logic in `reminder_runner.py`

```python
cv_data = reminder.get("cv_data")
if cv_data:
    # Fetch broad candidate set ignoring score filter
    candidates = get_jobs_for_reminder(keyword, min_score=0, max_jobs=200)
    # Score each against this reminder's CV
    scored = [(job, cv_score(job, cv_data)) for job in candidates]
    # Filter, sort, cap
    jobs = sorted(
        [(j, s) for j, s in scored if s >= min_score],
        key=lambda x: -x[1]
    )[:max_jobs]
    jobs = [j for j, _ in jobs]
else:
    # Legacy fallback: use relevance_score
    jobs = get_jobs_for_reminder(keyword, min_score, max_jobs)
```

Same logic applies to the manual "Send Now" route in `app.py`.

### Template changes (`templates/reminders.html`)

**Create form additions:**
- File input: `<input type="file" name="cv_file" accept=".pdf,.txt" required>`
- Label: "Your CV / Resume (PDF or TXT) — used to score job matches"
- Form `enctype="multipart/form-data"`

**Reminder card additions:**
- If `cv_data` present: green "CV ✓ · N skills" badge + "Update CV" button
- If `cv_data` absent: amber "No CV" badge
- "Update CV" opens a small inline form (POST to `/reminders/<id>/update-cv`)

---

## Helper function: `extract_cv_text(file_storage)`

Add a helper in `app.py` (or a new `cv_utils.py`) that takes a Werkzeug `FileStorage` object and returns raw text:

```python
def _extract_cv_text(file_storage) -> str:
    filename = file_storage.filename.lower()
    data = file_storage.read()
    if filename.endswith(".pdf"):
        from pdfminer.high_level import extract_text as pdf_extract
        from io import BytesIO
        return pdf_extract(BytesIO(data))
    else:
        return data.decode("utf-8", errors="replace")
```

---

## Error Handling

- No file uploaded at create time → server-side validation error, flash and redirect
- Unsupported file type → flash "Only PDF and TXT files are accepted"
- PDF extraction fails → flash error, do not create/update reminder
- Empty extracted text → flash "Could not extract text from the CV"
- `cv_score` always returns 0–100, never raises — safe to call per-job

---

## Files Changed

| File | Change |
|------|--------|
| `app.py` | Modify `/reminders/create` to handle file upload + CV extraction; add `/reminders/<id>/update-cv` route; add `_extract_cv_text()` helper |
| `reminder_runner.py` | Update `run_reminders()` to use CV-based scoring when `cv_data` present |
| `templates/reminders.html` | Add file input to create form (`enctype` + `required`); add CV badge + Update CV button to cards |
