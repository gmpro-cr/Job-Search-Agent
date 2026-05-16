---
name: frontend-design
description: Use whenever editing files under templates/ or static/css/main.css, building a new Jinja page or component, or adding UI to an existing route. Captures the project's design system (Outfit font, shadcn-inspired tokens), component class catalog, layout rules, accessibility expectations, and the visual landmines we've already stepped on.
---

# Frontend design skill — Job Search Agent

The app's frontend is **Jinja templates + a single hand-rolled CSS file** (`static/css/main.css`) modeled after shadcn/ui. There is **no React, no Tailwind, no build step**. Edit the CSS file directly and reference its classes from templates.

## When to invoke this skill

- Editing any file in `templates/`
- Editing `static/css/main.css` or `static/src/*`
- Adding a new page, sidebar item, or UI component
- Investigating a layout / styling bug
- Reviewing a UI change before commit

## How to start (checklist)

1. **Read `templates/base.html` first.** Every page extends it. Understand the sidebar, topbar, burger, flash messages, and mobile nav before adding content.
2. **Reuse the existing classes.** Catalog below. If you find yourself writing inline styles for spacing/typography that duplicate a utility class, stop and use the class.
3. **Match the visual language.** Outfit font, generous whitespace, subtle borders, `--radius` (0.625rem ≈ 10px) corners. No drop shadows except on hover (`box-shadow: 0 4px 16px rgba(0,0,0,.08)`).
4. **Mobile-first sanity check.** The layout has a mobile bottom nav (`<768px`) and an off-canvas sidebar. Don't break either.
5. **Run the page in a browser before claiming done.** `python app.py` → `http://localhost:5001` with `DATABASE_URL` set. Type checks don't catch layout bugs.

---

## Design tokens (CSS variables)

Defined in `:root` at the top of `main.css`. Use **the variable**, not the literal value, so theme changes stay in one place.

| Token | Value | Use for |
|---|---|---|
| `--background` | `#ffffff` | Page bg |
| `--foreground` | `#0d0d14` | Body text |
| `--card` | `#ffffff` | Card surfaces |
| `--primary` | `#030213` | Primary buttons, brand accents |
| `--primary-foreground` | `#ffffff` | Text on primary |
| `--secondary` | `#f0f0f8` | Secondary buttons, subtle highlights |
| `--muted` | `#ececf0` | Table headers, disabled bg |
| `--muted-foreground` | `#717182` | Helper text, labels |
| `--accent` | `#e9ebef` | Hover surfaces |
| `--destructive` | `#d4183d` | Delete / danger actions |
| `--border` | `rgba(0,0,0,0.10)` | Default divider |
| `--input-bg` | `#f3f3f5` | Form field background |
| `--ring` | `rgba(3,2,19,0.15)` | Focus ring |
| `--radius` | `0.625rem` | Default corner radius |
| `--sidebar*` | `#f9f9fb` family | Sidebar surfaces |

**Font:** `Outfit` (Google Fonts) at weights 300/400/500/600/700. Body line-height `1.5`. Don't introduce a second font without explicit user buy-in.

**Typography scale** (declared on `h1`–`h4`):

| Tag | Size | Weight |
|---|---|---|
| `h1` | `1.5rem` (24px) | 500 |
| `h2` | `1.25rem` (20px) | 500 |
| `h3` | `1rem` (16px) | 500 |
| `h4` | `0.9375rem` (15px) | 500 |

Body text is `16px`. Utility classes: `.text-sm` (14px), `.text-xs` (12px), `.text-2xl` (24px), `.text-muted`.

**Spacing scale** matches Tailwind defaults but lives as utilities only for vertical rhythm: `.space-y-{1,2,3,4,6}`. For one-off padding/margin, write inline `style="..."` rather than inventing new classes — that's the convention the rest of the codebase uses.

---

## Component class catalog

These all live in `main.css`. Reach for them **before** writing inline CSS.

### Layout shell

| Class | Purpose |
|---|---|
| `.layout` | `display:flex`, holds `.sidebar` + `.main-content`. Already in `base.html`. |
| `.sidebar` | Fixed 272px-wide left nav. **Don't widen further without updating `.main-content { margin-left }`.** |
| `.sidebar-link` / `.sidebar-link.active` | Nav items. Active state highlights the current page (matched via `request.endpoint`). |
| `.main-content` | Flex child. **Always keeps `min-width: 0`** so wide content compresses instead of overflowing the viewport. |
| `.topbar` | 57px-tall row with the burger button. |
| `.page-body` | 1.5rem padding wrapper, `overflow-x: hidden` as safety net. |

### Cards

```html
<div class="card">
  <div class="card-header">
    <div class="card-title">Title</div>
    <div class="card-description">Optional subtitle</div>
  </div>
  <div class="card-content">…body…</div>
</div>
```

Variants: `.card-header-row` (header laid out as flex row), `.card-content-pt` (content with header padding compensation).

### Buttons

`.btn` is the base; **always** combine with a variant:

| Variant | Use |
|---|---|
| `.btn-default` | Primary action — black background, white text |
| `.btn-outline` | Secondary action — bordered, transparent |
| `.btn-ghost` | Tertiary action — text-only, hover bg |
| `.btn-destructive` | Delete / dangerous action |

Size modifiers: `.btn-sm`, `.btn-lg`, `.btn-icon`, `.btn-icon-sm`. Disabled is `[disabled]` or `:disabled` (already styled).

> **Landmine:** several templates use `class="btn btn-primary"` but `.btn-primary` doesn't exist in `main.css` (the variant is `.btn-default`). Those buttons render with only the base `.btn` rules. If you touch one, change it to `btn-default` and consider adding a `.btn-primary` alias to `main.css` for back-compat.

### Badges

`.badge` + one of: `.badge-default`, `.badge-secondary`, `.badge-outline`, `.badge-success`, `.badge-warning`, `.badge-destructive`. Use `.score-high` / `.score-mid` / `.score-low` for the 0–100 relevance score widget.

### Forms

```html
<input class="input" type="text" placeholder="...">
<select class="input">...</select>
<textarea class="input"></textarea>
```

Wrap an icon+input pair in `.input-group` + `.input-icon` (icon absolutely positioned, input gets `padding-left: 2.5rem`).

### Tabs

`.tabs-list` (container), `.tabs-trigger` (button), `.tabs-trigger.active` (current), `.tabs-content` / `.tabs-content.active`. Switching is hand-rolled JS — see `templates/jobs.html` for the reference pattern.

### Flash messages

Server-rendered from `get_flashed_messages(with_categories=true)` in `base.html`. Categories `success` / `error` / `info` map to `.flash-success` / `.flash-error` / `.flash-info`. **Don't add categories without also adding the matching CSS class.**

### Tables

There's no `.table` class — use raw `<table>` with inline styles. For the canonical pattern see `templates/hiring_managers.html` (header row with `var(--muted)` bg, body rows with `border-top: 1px solid var(--border)`, vertical padding `.75rem 1rem`). Wrap any table that can overflow in `<div style="overflow-x:auto;">`.

### Modal

The job-detail modal in `templates/jobs.html` is the reference. Fixed overlay, max-width 48rem, click-outside to close via the outer `onclick="closeModal(event)"` + inner `onclick="event.stopPropagation()"`.

---

## Layout rules (the ones we've already broken)

1. **`min-width: 0` on every flex child whose content might be wide.** This includes `.main-content`, any `flex: 1` column inside a card, and the inner `div` of a job card holding the role title. Without it the flex item refuses to shrink past its intrinsic content width, and the page overflows horizontally while the sidebar is open.
2. **Sidebar width is single-source-of-truth in two places** — `.sidebar { width: 272px }` and `.main-content { margin-left: 272px }`. If you change one, change the other. Same number must appear in the `body.sidebar-collapsed` rules.
3. **Burger button** lives in the topbar and is visible on all viewports. Mobile (`≤767px`): toggles `.sidebar.open`. Desktop (`≥768px`): toggles `body.sidebar-collapsed` and persists to `localStorage`. The `<script>` in `<body>` of `base.html` that restores the collapsed state **must stay above the `.layout` div** so it runs before first paint and we never flash the wrong state.
4. **Mobile bottom nav** appears at `≤767px`; remember `padding-bottom: 5rem` on `.page-body` for that breakpoint so content isn't hidden under it.
5. **`@media (max-width: 767px)` is the mobile cutoff.** Anything narrower than that is "mobile"; everything else is "desktop". `.hide-mobile` hides at that breakpoint.
6. **`overflow-x: hidden` on `.page-body` is a safety net,** not a fix. The real fix is `min-width: 0` on the offending flex parent. Don't reach for `overflow-x: hidden` first.

---

## Adding a new page (the recipe)

1. Create `templates/your_page.html` extending `base.html`:

   ```jinja
   {% extends "base.html" %}
   {% block title %}Your Page - Job Search Agent{% endblock %}

   {% block content %}
   <div class="space-y-6">
     <div>
       <h1 style="font-size:1.5rem;font-weight:700;margin:0 0 .25rem;">Your Page</h1>
       <p class="text-muted">One-sentence purpose.</p>
     </div>

     <div class="card">
       <div class="card-content">…</div>
     </div>
   </div>
   {% endblock %}
   ```

2. Add a Flask route in `app.py` returning `render_template("your_page.html", …)`. Per-user routes call `current_user_id()` / `require_user_id()` — see [CLAUDE.md](../../../CLAUDE.md).
3. Add a sidebar entry in `templates/base.html`'s `<ul class="sidebar-menu">`:

   ```jinja
   <li>
     <a href="{{ url_for('your_route') }}"
        class="sidebar-link {% if request.endpoint == 'your_route' %}active{% endif %}">
       <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
         <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="…"/>
       </svg>
       Your Page
     </a>
   </li>
   ```

4. (Mobile) If the page is a top-3 destination, mirror it in `<nav class="mobile-nav">` at the bottom of `base.html`.
5. **Sanity-check the result in a real browser** at both desktop and mobile widths before pushing. Vercel auto-deploys on push; visual bugs ship as fast as code bugs.

---

## Sidebar icons

All sidebar icons are inline `<svg>` from Heroicons outline (stroke 2). When adding a new one, match the existing pattern:

```html
<svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="..."/>
</svg>
```

CSS already sizes them (`1.125rem` square, `opacity: 0.75` → `1` on hover/active). Don't add an inline `width`/`height` — it'll override the responsive sizing.

---

## Accessibility floor

- Buttons that look like buttons must be `<button>`. Anchors that navigate must be `<a href="…">`. Don't use `<div onclick=…>` for new code.
- Form inputs get a `<label>` (or `aria-label` if the label is iconographic).
- The burger has `aria-label="Toggle sidebar"` and `aria-controls="sidebar"`. Preserve those if you refactor the topbar.
- External links to LinkedIn / company sites must include `target="_blank" rel="noopener noreferrer"`.
- Contrast: foreground text on `--card` already passes WCAG AA. `--muted-foreground` is the lightest we go — don't add a lighter grey for body text.

---

## Don'ts

- **Don't add a CSS framework or a build step.** The hand-rolled CSS is intentional.
- **Don't put per-page styles in `<style>` blocks at the top of templates.** Either reuse a utility/component class or inline-style the one-off element.
- **Don't introduce `.btn-primary` styles** — the convention is `.btn-default`. If you need to support existing `btn-primary` templates, add a one-line `.btn-primary { @apply .btn-default; }`-equivalent alias rather than two variants.
- **Don't wrap a Flask `Response` with `mimetype="text/html; charset=utf-8"`** — that gives you `; charset=utf-8; charset=utf-8`. Use `content_type=` instead. (We already fixed `serve_digest`; don't reintroduce.)
- **Don't fetch and re-render entire pages client-side** unless the existing template already does so (`/jobs` is the only one). Server-rendered Jinja is the norm.
- **Don't centralise spacing into new utility classes** before the same value appears 5+ times across templates. Inline `style="..."` is the convention.

---

## Reference templates by use case

| If you're building… | Look at |
|---|---|
| A dashboard with KPI tiles + side cards | `templates/dashboard.html` |
| A filter sidebar + main list (client-side render) | `templates/jobs.html` |
| A simple data table with rows + actions | `templates/hiring_managers.html` |
| A long form with sections | `templates/preferences.html` |
| A list page with a server-rendered grid | `templates/digests.html` |
| A "wizard-style" multi-step layout | `templates/setup.html` |
| Public unauthenticated page | `templates/login.html` |

---

## Performance hygiene

- Inline SVGs are fine. There's no icon font.
- Don't load Tailwind or any external CSS. The one external dep is the Outfit Google Font (`@import` at top of `main.css`).
- Heavy client-side JS lives at the bottom of the relevant template inside `{% block scripts %}`. Don't bloat `base.html`.
- For pages that render >100 rows server-side, prefer client-side rendering via a `/api/...` JSON endpoint (the `/jobs` pattern). Keep payloads under ~200KB.
