# Conversion-oriented scoring — design (2026-10-21)

## Problem

Scores measured *topical* fit: does the JD mention the candidate's titles,
skills and differentiators? They didn't measure whether an application is
likely to *convert* (get a screen). `docs/CANDIDATE_CONTEXT.md` records zero
shortlists on the previous CV and a list of fit rules learned the hard way:
roles that look relevant but don't convert for this candidate (Director/Head
titles, JDs demanding years of formal PM tenure, retail-mortgage risk, ML
engineering, PM roles inside banks, relocation, scam postings), and the ones
that do (Indian credit depth, AI applied to BFSI, voice AI in banking,
a short list of target companies).

Separately, the app could not learn from outcomes: `delete_old_jobs(7)`
deleted `user_job_state` rows, Applied / Interview / Offer included, once a
listing aged out.

## Design

Two layers on top of the existing bands. Both show up as named lines in the
score breakdown (`bd["adjustments"]`), so nothing is folded in invisibly.

### 1. Fit rules (`scorer._fit_adjustments`)

Opt-in per scoring profile (`SCORING_PROFILES[...]["fit_rules"]`):
`bfsi_ai` uses all of them, `project_delivery` only `red_flag` and
`target_company`. The sum is clamped to **-30..+20**.

| Rule | Points | Reads |
|---|---|---|
| Too senior (Director/Head/Chief; VP/GPM on the product track only) | -12 | title |
| Experience floor ≥ candidate years + 4 | -10 | `experience_min` vs prefs `years_experience` / CV |
| Needs ≥ 4 yrs of *product management* (product track) | -8 | JD |
| Retail / consumer risk (mortgage, home loans, consumer risk) | -10 | title, or a JD requirement |
| Engineering / research role | -20 | title |
| Bank employer on the PM track (not a target company) | -8 | company |
| Relocation needed (not remote, not a preferred city) | -10 | location |
| Red flag: personal-mail recruiter, fee language | -15 | `poster_email`, JD |
| Credit moat (CAM, MPBF, DSCR, drawing power, underwriting, …) | +4/term, ≤ +10 | JD |
| AI × BFSI (both bands present) | +5 | bands |
| Voice / conversational AI with a BFSI signal | +5 | JD |
| Target company (prefs `target_companies`, else profile default list) | +10 | company |

The "track" is banking (RM / credit titles) or product (everything else).
On the banking track VP/AVP titles, bank employers and PM tenure don't count.
Engineering, retail-risk and red-flag postings get **no boosts**: domain
vocabulary or a target name doesn't make a wrong-shaped role a good bet.

### 2. Outcome model (`outcome_model.py`)

Learns per user from `user_outcome_examples`, a snapshot table written on
every status/hide change and before each retention sweep, so history now
survives. It tracks `max_status`, so "interview, then rejected" still counts
as a callback.

- **Conversion** (±12): positive = reached phone screen or later; negative =
  rejected without a screen, or applied and silent for 21 days (half weight).
- **Interest** (±5): kept (saved/applied/progressed) vs hidden.

Features are interpretable (track, seniority, portal, company, band
strength, "carries neither differentiator", fired fit rules). Effects are
naive-Bayes log-odds with Beta shrinkage to the user's own rate. The sum
over a job's features is divided by √k, squashed with tanh, and ramped by
n/(n+10). A signal stays **off until it has ≥ 2 weighted examples on each
side**. With only rejections there is no contrast, and every feature of the
candidate's target roles would read as negative.

The model's fingerprint is part of the scoring stamp, so the scrape
re-scores a user's jobs when their outcomes change. `/api/scoring/insights`
shows what the model has learned.

## Not done

- Freshness: dead/closed listings (`apply_url` checks) and posting age.
  Early applications convert better, but stored scores would go stale.
- Embedding-based outcome similarity: job vectors exist, but the app-side
  rescore paths don't load them.
- The tracker itself still loses old Applied/Interview rows from
  `user_job_state` after 7 days. Only the learning snapshot is kept.
