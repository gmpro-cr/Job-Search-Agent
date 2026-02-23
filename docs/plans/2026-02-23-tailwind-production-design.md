# Tailwind CSS Production Setup — Design Document

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan.

**Goal:** Replace the Tailwind CDN script with a pre-built static CSS file served by Flask.

**Date:** 2026-02-23

---

## Problem

`base.html` loads Tailwind from `https://cdn.tailwindcss.com`. This is unsuitable for production: it ships the full 3 MB development build, logs a console warning, and requires an external network request on every page load.

---

## Approach

**Tailwind CLI (Option A):** Build `static/css/tailwind.css` locally using `npx tailwindcss`, commit the output, and serve it as a Flask static file. The Dockerfile needs no changes.

---

## Files

| File | Change |
|------|--------|
| `tailwind.config.js` | New — Tailwind config with `brand` color palette and `content` glob pointing at all templates |
| `static/src/input.css` | New — three `@tailwind` directives |
| `static/css/tailwind.css` | Generated and committed — minified production build |
| `package.json` | New — `build:css` script |
| `.gitignore` | Add `node_modules/` |
| `templates/base.html` | Replace CDN `<script>` + inline config with `<link rel="stylesheet" href="{{ url_for('static', filename='css/tailwind.css') }}">` |

---

## Tailwind Config

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

---

## Build Command

```bash
npm run build:css
# equivalent to:
npx tailwindcss -i static/src/input.css -o static/css/tailwind.css --minify
```

Run this locally whenever templates change, then commit the updated CSS before deploying.

---

## Constraints

- Dockerfile unchanged — CSS is pre-built and committed, no Node needed in the container
- `node_modules/` added to `.gitignore`
- `static/css/tailwind.css` **is** committed (it's a build artifact but it's the deployment artifact)
