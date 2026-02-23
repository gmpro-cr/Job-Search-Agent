# Tailwind CSS Production Setup — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the Tailwind CDN `<script>` with a pre-built static CSS file served by Flask.

**Architecture:** Run `npx tailwindcss` locally to compile `static/css/tailwind.css` from all HTML templates. Commit the output. Flask serves it as a static file. Dockerfile unchanged.

**Tech Stack:** Tailwind CSS CLI (via npx), Flask static files, Jinja2 templates.

---

## Context: Key facts before starting

- `templates/base.html` line 7: `<script src="https://cdn.tailwindcss.com"></script>` — to be removed
- `templates/base.html` lines 8-18: inline `tailwind.config` JS block — to be removed
- `templates/base.html` lines 20-25: a `<style>` block with hand-written CSS (`.sidebar-link`, `.stat-card`) — **keep this unchanged**
- Project root: `/Users/gaurav/job-search-agent`
- Node v22 and npm 11 are available locally
- No `package.json` or `node_modules/` exist yet
- No `static/` directory exists yet
- `.gitignore` exists — `node_modules/` needs to be added

---

## Task 1: Scaffold `package.json` and `tailwind.config.js`

**Files:**
- Create: `package.json`
- Create: `tailwind.config.js`
- Modify: `.gitignore`

**Step 1:** Create `package.json`:

```json
{
  "name": "job-search-agent",
  "private": true,
  "scripts": {
    "build:css": "tailwindcss -i static/src/input.css -o static/css/tailwind.css --minify"
  },
  "devDependencies": {
    "tailwindcss": "^3.4.0"
  }
}
```

**Step 2:** Create `tailwind.config.js`:

```js
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        brand: {
          50:  '#eef2ff',
          100: '#e0e7ff',
          500: '#6366f1',
          600: '#4f46e5',
          700: '#4338ca',
        }
      }
    }
  },
  plugins: [],
}
```

**Step 3:** Add `node_modules/` to `.gitignore`. Append to the existing file:

```
node_modules/
```

**Step 4:** Install Tailwind:

```bash
cd /Users/gaurav/job-search-agent && npm install
```

Expected: `node_modules/` created, `package-lock.json` created. No errors.

**Step 5:** Verify Tailwind CLI works:

```bash
cd /Users/gaurav/job-search-agent && npx tailwindcss --version
```

Expected: prints something like `3.4.x`

**Step 6:** Commit:

```bash
cd /Users/gaurav/job-search-agent && git add package.json tailwind.config.js .gitignore package-lock.json && git commit -m "build: add tailwind CLI config"
```

---

## Task 2: Create input CSS and build the output file

**Files:**
- Create: `static/src/input.css`
- Create (generated): `static/css/tailwind.css`

**Step 1:** Create `static/src/input.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

**Step 2:** Build the CSS:

```bash
cd /Users/gaurav/job-search-agent && npm run build:css
```

Expected output (roughly):
```
Rebuilding...
Done in Xms.
```

`static/css/tailwind.css` should now exist and contain minified CSS.

**Step 3:** Verify the file was created and is non-empty:

```bash
wc -c /Users/gaurav/job-search-agent/static/css/tailwind.css
```

Expected: a number greater than 1000 (typically 10,000–20,000 bytes for a minified build scanning real templates).

**Step 4:** Commit:

```bash
cd /Users/gaurav/job-search-agent && git add static/src/input.css static/css/tailwind.css && git commit -m "build: add tailwind input CSS and initial production build"
```

---

## Task 3: Update `base.html` to load local CSS

**Files:**
- Modify: `templates/base.html` (lines 7–18)

**Step 1:** Find and replace the CDN script + inline config block in `templates/base.html`.

Current lines 7–18:
```html
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            brand: { 50:'#eef2ff', 100:'#e0e7ff', 500:'#6366f1', 600:'#4f46e5', 700:'#4338ca' }
          }
        }
      }
    }
  </script>
```

Replace with:
```html
  <link rel="stylesheet" href="{{ url_for('static', filename='css/tailwind.css') }}">
```

**Important:** The `<style>` block on the next lines (`.sidebar-link`, `.stat-card`, `body { font-family... }`) must be left completely unchanged.

**Step 2:** Verify the app renders without the CDN warning:

```bash
cd /Users/gaurav/job-search-agent && python3 -c "
import app
with app.app.test_client() as c:
    r = c.get('/')
    html = r.data.decode()
    assert 'cdn.tailwindcss.com' not in html, 'CDN script still present!'
    assert 'css/tailwind.css' in html, 'Local CSS link missing!'
    print('OK')
"
```

Expected: `OK`

**Step 3:** Commit:

```bash
cd /Users/gaurav/job-search-agent && git add templates/base.html && git commit -m "feat: replace tailwind CDN with local production build"
```

---

## Final Verification

Start the app and open it in a browser:

```bash
cd /Users/gaurav/job-search-agent && python3 app.py
```

- Open `http://localhost:5001` (or whatever port is configured)
- Check the browser console — the `cdn.tailwindcss.com should not be used in production` warning should be gone
- Verify the UI looks identical to before (sidebar, cards, badges, buttons all styled)
- Check Network tab: `tailwind.css` loads from `/static/css/tailwind.css`

---

## Rebuilding CSS after template changes

Whenever you edit HTML templates and add new Tailwind classes, rebuild:

```bash
npm run build:css
git add static/css/tailwind.css && git commit -m "build: rebuild tailwind CSS"
```
