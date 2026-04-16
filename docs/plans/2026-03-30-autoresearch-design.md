# Autoresearch: Self-Improving CV Scoring

**Date:** 2026-03-30
**Inspired by:** Karpathy's autoresearch (github.com/karpathy/autoresearch)

## Goal

Give the job search agent a closed feedback loop that autonomously improves its own CV-scoring prompt overnight — with no human in the loop per iteration.

## Architecture

Three files with strict ownership:

| File | Owner | Purpose |
|---|---|---|
| `autoresearch/evaluate.py` | **Locked (human)** | Loads frozen test set, scores 30 jobs with current prompt, returns Spearman rank correlation |
| `autoresearch/scoring_prompt.md` | **Agent editable** | The LLM system prompt for job scoring (extracted from nodes.py) |
| `autoresearch/loop.py` | **Orchestrator** | Reads prompt + git log → LLM hypothesis → edit → evaluate → commit or reset |

Support files:
- `autoresearch/seed.py` — runs once to create frozen `testset.json`
- `autoresearch/testset.json` — 30 jobs + ground truth scores (never modified)
- `autoresearch/baseline.json` — initial Spearman score before any experiments
- `autoresearch/results.json` — running experiment log

## Evaluation

**Metric:** Spearman rank correlation between agent scores and ground truth scores on the 30-job frozen test set.

- 1.0 = perfect ranking, 0.0 = random, negative = inverted
- Measures *ordering* quality, not absolute values
- Directly reflects what matters: does the agent rank good jobs higher?

**Test set seeding (seed.py, runs once):**
1. Sample 30 jobs from DB — spread across high/mid/low relevance scores and role categories
2. Call LLM with full CV + each JD: "Rate 0–100 for this candidate. Return {score, reason}"
3. Save to testset.json — frozen forever

**Future:** When ≥20 user-labeled jobs accumulate (applied_status=1 or hidden=1), they optionally augment or replace the test set.

## The Loop

Each iteration (~30–60 seconds):

```
1. Read scoring_prompt.md + last 10 experiment outcomes from results.json
2. Call LLM: propose one change to improve Spearman score
   → returns {hypothesis, new_prompt}
3. Write new_prompt → scoring_prompt.md
4. Run evaluate.py → new_score
5. If new_score > best_score:
     git commit "experiment #N: +{delta:.3f} spearman — {hypothesis}"
     best_score = new_score
6. Else:
     git checkout scoring_prompt.md  (reset, no commit)
7. Append to results.json
8. Repeat
```

**LLM context per iteration:**
- Current prompt text
- Last 10 experiment outcomes (hypothesis + delta + outcome)
- Baseline and current best Spearman score
- CV summary (so it understands "relevant")

**Stop conditions:** `--max-experiments N`, manual interrupt, or 20 consecutive failures (plateau detection).

**Safety:** Loop validates that only `scoring_prompt.md` is modified before committing.

## UI (Flask page: /autoresearch)

- Status card: running/idle, current best score vs baseline
- Experiment log: hypothesis, delta, outcome per experiment
- Prompt diff: current prompt vs baseline (what the agent learned)
- Controls: Run 1, Run 100, Stop, Reset to baseline
- Background thread with SSE/polling for live progress

## Integration with Existing Agent

`agent/nodes.py` `llm_score_and_filter` loads its system prompt from `autoresearch/scoring_prompt.md` at runtime instead of the hardcoded string — so every improvement is automatically used in production.
