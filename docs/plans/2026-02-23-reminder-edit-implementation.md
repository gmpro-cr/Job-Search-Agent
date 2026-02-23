# Reminder Edit — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an inline edit form to each reminder card so users can update name, keyword, email, min_score, and max_jobs without recreating the reminder.

**Architecture:** A new `POST /reminders/<id>/edit` Flask route updates the five editable fields in `reminders.json`. Each reminder card gets an "Edit" button that toggles a hidden `<div>` containing a pre-filled form; a Cancel button re-hides it. No new template file needed.

**Tech Stack:** Flask, Jinja2, `reminders.json` via `reminder_runner.py`, Tailwind CSS, vanilla JS (single `classList.toggle` call).

---

## Context: Key facts before starting

- `app.py` line 1338 ends the last reminder route (`reminders_update_cv`); line 1340 is `# Run` section
- New route goes between line 1338 and line 1340 (same pattern as all other reminder routes)
- `reminders_toggle` (lines 1249–1259) is the simplest existing route — use it as a structural template
- `reminders_update_cv` (lines 1307–1337) uses `try/except OSError` around `save_reminders` — copy this pattern
- `templates/reminders.html` line 93: `<!-- Actions -->` div — Edit button goes inside here, before the Send Now form
- `templates/reminders.html` line 123: closing `</div>` of each card — inline edit form goes immediately after this line
- Fields NOT to touch: `cv_data`, `enabled`, `last_sent`, `id`

---

## Task 1: Add `POST /reminders/<id>/edit` route to `app.py`

**Files:**
- Modify: `app.py` (insert before line 1340 `# Run` section)

**Step 1:** Read `app.py` lines 1336–1342 to confirm the exact text just before `# ---------------------------------------------------------------------------\n# Run`.

**Step 2:** Insert this route immediately before the `# Run` section:

```python
@app.route("/reminders/<reminder_id>/edit", methods=["POST"])
def reminders_edit(reminder_id):
    """Update editable fields of an existing reminder."""
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

    all_reminders = load_reminders()
    updated = False
    for r in all_reminders:
        if r.get("id") == reminder_id:
            r["name"] = name
            r["keyword"] = keyword
            r["email"] = email_addr
            r["min_score"] = min_score
            r["max_jobs"] = max_jobs
            updated = True
            break
    if updated:
        try:
            save_reminders(all_reminders)
        except OSError as e:
            flash(f"Failed to save reminder: {e}", "error")
            return redirect(url_for("reminders"))
        flash(f"Reminder '{name}' updated.", "success")
    else:
        flash("Reminder not found.", "error")
    return redirect(url_for("reminders"))
```

**Step 3:** Verify the route is registered:

```bash
cd /Users/gaurav/job-search-agent && python3 -c "
import app
rules = sorted([str(r) for r in app.app.url_map.iter_rules() if 'reminder' in str(r)])
for r in rules: print(r)
"
```

Expected: 7 lines including `/reminders/<reminder_id>/edit`.

**Step 4:** Commit:

```bash
cd /Users/gaurav/job-search-agent && git add app.py && git commit -m "feat: add reminders edit route"
```

---

## Task 2: Add Edit button and inline form to `templates/reminders.html`

**Files:**
- Modify: `templates/reminders.html`

### 2a — Add Edit button to the actions div

**Step 1:** Find the actions div opening (line ~94):

```html
      <!-- Actions -->
      <div class="flex items-center gap-2 flex-shrink-0 flex-wrap">
        <form method="POST" action="{{ url_for('reminders_update_cv', reminder_id=r.id) }}"
```

Insert the Edit button as the **first** child of the actions div, before the Update CV form:

```html
      <!-- Actions -->
      <div class="flex items-center gap-2 flex-shrink-0 flex-wrap">
        <button type="button"
                onclick="document.getElementById('edit-{{ r.id }}').classList.toggle('hidden')"
                class="text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 font-medium transition-colors">
          Edit
        </button>
        <form method="POST" action="{{ url_for('reminders_update_cv', reminder_id=r.id) }}"
```

### 2b — Add the inline edit form after each card

**Step 2:** Find the closing `</div>` of each card (line ~123, the one that closes `<div class="bg-white border border-gray-200 rounded-xl p-4 ...`). It looks like:

```html
    </div>
    {% endfor %}
```

Insert the edit form between `</div>` and `{% endfor %}`:

```html
    </div>
    <div id="edit-{{ r.id }}" class="hidden mt-1 bg-gray-50 border border-gray-200 rounded-xl p-4">
      <h3 class="text-xs font-semibold text-gray-600 mb-3">Edit Reminder</h3>
      <form method="POST" action="{{ url_for('reminders_edit', reminder_id=r.id) }}">
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-3">
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Alert name</label>
            <input type="text" name="name" value="{{ r.name }}" required
                   class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Job title keyword</label>
            <input type="text" name="keyword" value="{{ r.keyword }}" required
                   class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Recipient email</label>
            <input type="email" name="email" value="{{ r.email }}" required
                   class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Min match score (0–100)</label>
            <input type="number" name="min_score" value="{{ r.min_score }}" min="0" max="100"
                   class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
          </div>
          <div>
            <label class="block text-xs font-medium text-gray-600 mb-1">Max jobs per email (1–50)</label>
            <input type="number" name="max_jobs" value="{{ r.max_jobs }}" min="1" max="50"
                   class="w-full text-sm rounded-lg border border-gray-200 focus:border-indigo-400 focus:ring-indigo-400 py-2 px-3">
          </div>
        </div>
        <div class="flex gap-2">
          <button type="submit"
                  class="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold rounded-lg transition-colors">
            Save
          </button>
          <button type="button"
                  onclick="document.getElementById('edit-{{ r.id }}').classList.add('hidden')"
                  class="px-4 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 text-xs font-medium transition-colors">
            Cancel
          </button>
        </div>
      </form>
    </div>
    {% endfor %}
```

### 2c — Verify

```bash
cd /Users/gaurav/job-search-agent && python3 -c "
import app
with app.app.test_client() as c:
    r = c.get('/reminders')
    assert r.status_code == 200
    assert b'reminders_edit' in r.data or b'/edit' in r.data
    assert b'edit-' in r.data
    print('OK')
"
```

Expected: `OK`

**Step 3:** Commit:

```bash
cd /Users/gaurav/job-search-agent && git add templates/reminders.html && git commit -m "feat: add inline edit form to reminder cards"
```

---

## Final Verification Checklist

Start the app and verify manually:

```bash
cd /Users/gaurav/job-search-agent && python3 app.py
```

- [ ] `http://localhost:5001/reminders` loads without errors
- [ ] Each reminder card has an "Edit" button
- [ ] Clicking "Edit" reveals the inline form pre-filled with current values
- [ ] Clicking "Cancel" hides the form
- [ ] Saving with valid data flashes `"Reminder 'X' updated."` and reflects new values in the card
- [ ] Saving with blank name/keyword/email flashes the validation error
- [ ] All other buttons (Send Now, Pause, Update CV, Delete) still work
