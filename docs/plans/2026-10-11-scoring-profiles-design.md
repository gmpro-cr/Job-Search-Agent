# Per-candidate scoring profiles

## Problem

The 2026-09-23 BFSI/AI reweighting made BFSI and AI the two differentiator
bands for **every** user, with a -15 penalty when a JD mentions neither. Those
are the owner's differentiators. For a project manager (user 10, Purva), a
typical IT project-manager JD capped at ~50 (title 30 + location 10 +
skills 20 + industry 5 - 15), below her routine's minimum score. Her routine
emailed nothing from the first run on the new scorer (2026-09-24) onwards; the
last send was 2026-09-22 (20 jobs).

## Design

`scoring_maps.SCORING_PROFILES` names the two differentiator bands per profile:

| Profile | Bands (0-20 each) | Penalty label |
|---|---|---|
| `bfsi_ai` (default) | BFSI Fit, AI Fit | No BFSI/AI |
| `project_delivery` | Delivery Methods (agile, scrum, sprint, release mgmt, ...), PM Governance (PMP, PRINCE2, RAID, resource planning, ...) | No delivery signal |

Title 30 / location 10 / CV skills 20 / industry 5 / -15 penalty are shared, so
both profiles use the same 0-100 scale.

`project_delivery` bands are **CV-scoped**: they only credit terms that also
appear in the candidate's CV, because agile appears in nearly every IT PM JD and
would otherwise score everyone the full band. A CV mentioning none of a band's
terms falls back to the whole table.

### Choosing a profile

`scorer.resolve_scoring_profile(cv_data, prefs)`:

1. `prefs["scoring_profile"]` if it names a profile (set from the Jobs page
   Preferences modal: "Auto from CV" / a named profile).
2. Otherwise count distinct profile signal terms in the CV text + skills +
   target titles. The highest count wins; ties go to `bfsi_ai`. Generic terms
   (risk management, portfolio management, bank, jira, ...) are excluded,
   so a PM CV naming a banking client stays `project_delivery`.

### Rescoring

Stored per-user scores are on the old scale. `scorer.scoring_stamp()` =
`SCORER_VERSION:profile`. The scrape (Phase 7b) compares it with the user's
`prefs["_scored_with"]`; on mismatch it fully rescores that user's jobs once and
writes the stamp (`database.set_user_scoring_stamp`, which leaves the CV
embedding alone and skips users without a preferences row). Bump
`SCORER_VERSION` on future scoring changes to trigger the same rebuild.

## Known limits

- The score-breakdown panel reads `job_listings.score_breakdown`, which is
  scored against the owner's profile, not the viewer's (unchanged).
