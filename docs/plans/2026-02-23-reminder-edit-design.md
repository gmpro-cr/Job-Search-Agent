# Reminder Edit — Design Document

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Allow users to edit reminder fields (name, keyword, email, min_score, max_jobs) inline without recreating the reminder.

**Date:** 2026-02-23

---

## Scope

- Editable fields: `name`, `keyword`, `email`, `min_score`, `max_jobs`
- Not editable via this form: `cv_data`, `enabled`, `last_sent`, `id`
- CV remains updatable via the existing "Update CV" button

---

## Backend

### New route: `POST /reminders/<id>/edit`

- Reads `name`, `keyword`, `email`, `min_score`, `max_jobs` from `request.form`
- Validates: name/keyword/email required; min_score clamped 0–100; max_jobs clamped 1–50
- Finds reminder by `id` in `reminders.json`, updates only the five fields, saves
- Flashes success message, redirects to `url_for('reminders')`
- If reminder not found: flashes error, redirects

---

## Frontend

### Edit button on each card

Added to the actions div (alongside Send Now / Pause / Delete):

```html
<button type="button"
        onclick="document.getElementById('edit-{{ r.id }}').classList.toggle('hidden')"
        class="text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 font-medium transition-colors">
  Edit
</button>
```

### Inline edit form (hidden by default)

Placed immediately after the card's closing `</div>`, inside the `{% for r in reminders %}` loop:

```html
<div id="edit-{{ r.id }}" class="hidden mt-2 bg-gray-50 border border-gray-200 rounded-xl p-4">
  <form method="POST" action="{{ url_for('reminders_edit', reminder_id=r.id) }}">
    <!-- 5 fields pre-filled with current values -->
    <!-- Save + Cancel buttons -->
  </form>
</div>
```

Fields: name, keyword, email (pre-filled), min_score, max_jobs (pre-filled with current values).

Cancel button: `onclick="document.getElementById('edit-{{ r.id }}').classList.add('hidden'); return false;"` — hides the form without submitting.

---

## Files Changed

| File | Change |
|------|--------|
| `app.py` | Add `POST /reminders/<id>/edit` route |
| `templates/reminders.html` | Add Edit button + inline form to each card |
