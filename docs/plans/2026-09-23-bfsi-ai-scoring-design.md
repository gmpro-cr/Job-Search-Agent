# BFSI + AI background scoring — design

Date: 2026-09-23

## Problem

`scorer.deterministic_score` collapsed every industry into a single 0-10
`domain` band. Fintech/banking, AI/ML, SaaS and ecommerce each scored 5 pts per
hit, capped at 10 — so the candidate's two differentiators (9 yrs banking /
credit risk, three shipped LLM products) were worth at most 10% of the score
and were indistinguishable from each other.

Compounding it: generic PM skills (roadmap, A/B testing, agile, stakeholder
management) hit on nearly every PM JD, so any PM role in a preferred city
floored around 70 before domain was considered. A 0-10 band could not move
ranking regardless of weighting.

## Decisions

1. **Hybrid strictness.** BFSI and AI get their own weighted bands. A posting
   with neither takes a -15 penalty but is never hard-zeroed — a strong
   generalist PM role can still clear the threshold on its other bands.
2. **Requirement beats sector.** A JD that asks for the background scores the
   full 20; a posting that merely operates in the sector scores 10. Detected by
   looking for a requirement cue within 60 characters of the matched term.
3. **Seniority grading removed entirely** — both `seniority_penalty` and the
   `experience` +5. They were one mechanism, and keeping a one-sided junior
   bonus would still be grading by seniority. `experience_min` /
   `experience_max` are still extracted in `analyzer.py` and remain filterable
   (`app.py:1331`), so the data is preserved as an explicit user control rather
   than folded invisibly into the score.

## Bands

| Band              | Before | After |
|-------------------|--------|-------|
| title             | 0-45   | 0-30  |
| location          | 0-15   | 0-10  |
| cv_skills         | 0-30   | 0-20  |
| **bfsi**          | —      | **0-20** |
| **ai**            | —      | **0-20** |
| domain (generic)  | 0-10   | 0-5   |
| experience        | 0-5    | removed |
| seniority_penalty | -15    | removed |
| **no_domain_penalty** | —  | **-15** |

Max 105, clipped to 100. Title strong=30 / partial=20 preserves the old 45/30
ratio. `SEMANTIC_BONUS_MAX` (+20) still applies on top in `score_job`.

Generic-industry terms that also appear in the BFSI or AI tables are subtracted
before scoring the generic band, so fintech cannot be counted twice.

## Bug fixed along the way

`scorer.py` tested domain hits with a plain substring `d in text`. The AI
cluster contained the bare token `"ai"`, so `"ai" in "email marketing"` was
`True` — every JD mentioning *email*, *retail*, *available* or *maintain*
scored an AI/ML hit. Domain matching now goes through
`scoring_maps.term_in_text`, which uses a cached `\b`-anchored regex. Terms with
non-word edges (`a.i.`, `ai/ml`) fall back to plain escaped matching.

Regression tests: `test_substring_false_positive_does_not_score_ai`,
`test_word_boundary_match_avoids_substring_hits`.

## Measured effect

Scored against the real `cv_data.json` + `user_preferences.json`
(deterministic component only, before the semantic lift):

| Job | Before | After |
|---|---|---|
| AI PM, lending, JD asks both | 100 | 90 |
| AI PM at AI startup, remote | 90 | 70 |
| PM, fintech, Bangalore (sector only) | 85 | 65 |
| PM, e-commerce, Pune | 65 | 45 |
| PM, "email marketing / retail" SaaS | 60 | 35 |
| Head of Product, fintech, 12 yrs | 45 | 70 |
| Registered Nurse | 0 | 0 |

The e-commerce and email-marketing rows now fall below the 55-point digest
cutoff. The Head-of-Product row is the accepted trade-off of removing seniority
grading: senior BFSI roles stop being auto-docked and will start surfacing.

## Files touched

- `scoring_maps.py` — `BFSI_TERMS`, `AI_TERMS`, `domain_terms()`,
  `term_pattern()`, `term_in_text()`; BFSI/AI removed from `_INDUSTRY_CLUSTERS`.
- `scorer.py` — `_band_score()`, `_REQ_CUE`, reweighted bands, seniority block
  deleted.
- `templates/_job_card_list.html` — breakdown panel rewritten (it was already
  stale: it listed a `pm_keywords` band that no longer existed and maxes that
  matched no current weight). Now shows the matched BFSI/AI evidence term.
- `tests/test_scorer.py`, `tests/test_scoring_maps.py`.
