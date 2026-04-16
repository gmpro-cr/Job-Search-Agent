# Autoresearch: Self-Improving CV Scoring — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a closed feedback loop that autonomously improves the job-scoring LLM prompt overnight using Karpathy's autoresearch pattern (hypothesis → edit → evaluate → commit or reset).

**Architecture:** Extract the scoring prompt to `autoresearch/scoring_prompt.md`. A locked `evaluate.py` measures Spearman rank correlation on a frozen 30-job test set. `loop.py` runs the improvement cycle, git-committing only improvements.

**Tech Stack:** Python stdlib, scipy (Spearman), existing `agent/llm.py` (OpenRouter/Ollama), SQLite, git CLI via subprocess, Flask for UI.

---

### Task 1: Create the autoresearch package and extract the scoring prompt

**Files:**
- Create: `autoresearch/__init__.py`
- Create: `autoresearch/scoring_prompt.md`
- Modify: `agent/nodes.py` (load prompt from file instead of hardcoded string)

**Step 1: Create the package init**

```bash
mkdir -p autoresearch
touch autoresearch/__init__.py
```

**Step 2: Write `autoresearch/scoring_prompt.md`**

Extract the exact prompt template from `agent/nodes.py:70-82` into the file. The file contains ONLY the system instructions (no f-string variables). Variables `{role}`, `{company}`, `{jd}`, `{cv_skills}`, `{cv_summary}` are interpolated at runtime.

```markdown
Score this job against the candidate's CV on a scale of 0-100.

JOB:
Role: {role}
Company: {company}
Description: {jd}

CANDIDATE CV SUMMARY:
Skills: {cv_skills}
Background: {cv_summary}

Return ONLY valid JSON in this exact format:
{"score": <integer 0-100>, "reason": "<one sentence why this is or isn't a good fit>"}
```

**Step 3: Modify `agent/nodes.py` to load from file**

Replace the hardcoded `prompt = f"""..."""` block in `llm_score_and_filter` (lines 70-82) with:

```python
import os as _os

_PROMPT_PATH = _os.path.join(
    _os.path.dirname(__file__), "..", "autoresearch", "scoring_prompt.md"
)

def _load_scoring_prompt(role, company, jd, cv_skills, cv_summary):
    """Load scoring prompt template from file and interpolate variables."""
    try:
        with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
            template = f.read()
        return template.format(
            role=role, company=company, jd=jd,
            cv_skills=cv_skills, cv_summary=cv_summary
        )
    except Exception:
        # Fallback to hardcoded if file missing
        return f"""Score this job against the candidate's CV on a scale of 0-100.

JOB:
Role: {role}
Company: {company}
Description: {jd}

CANDIDATE CV SUMMARY:
Skills: {cv_skills}
Background: {cv_summary}

Return ONLY valid JSON in this exact format:
{{"score": <integer 0-100>, "reason": "<one sentence why this is or isn't a good fit>"}}"""
```

Then replace the `prompt = f"""..."""` call in `llm_score_and_filter` with:
```python
prompt = _load_scoring_prompt(role, company, jd, cv_skills, cv_summary)
```

**Step 4: Verify the agent still works**

```bash
cd /Users/gaurav/job-search-agent
python3 -c "
from agent.nodes import _load_scoring_prompt
p = _load_scoring_prompt('PM', 'Acme', 'Build products', 'SQL', 'Banker')
print(p[:80])
assert 'PM' in p
assert 'Acme' in p
print('OK')
"
```
Expected: prints first 80 chars of prompt and `OK`.

**Step 5: Commit**

```bash
git add autoresearch/__init__.py autoresearch/scoring_prompt.md agent/nodes.py
git commit -m "feat: extract scoring prompt to autoresearch/scoring_prompt.md"
```

---

### Task 2: Write the locked evaluation function

**Files:**
- Create: `autoresearch/evaluate.py`
- Create: `tests/test_autoresearch.py`

**Step 1: Write the failing test first**

```python
# tests/test_autoresearch.py
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_evaluate_returns_float_between_neg1_and_1(tmp_path, monkeypatch):
    """evaluate() returns Spearman correlation in [-1, 1]."""
    # Seed a tiny testset
    testset = [
        {"job_id": f"j{i}", "role": "PM", "company": "Acme",
         "job_description": "Build products", "ground_truth_score": i * 10}
        for i in range(1, 6)
    ]
    testset_path = tmp_path / "testset.json"
    testset_path.write_text(json.dumps(testset))

    # Mock call_llm_json to return scores in same order as ground truth
    scores = iter([55, 44, 33, 22, 11])
    monkeypatch.setattr(
        "autoresearch.evaluate.call_llm_json",
        lambda prompt: {"score": next(scores), "reason": "test"}
    )

    from autoresearch.evaluate import evaluate
    result = evaluate(
        testset_path=str(testset_path),
        prompt_path=None,  # uses default
        cv_skills="SQL", cv_summary="Banker"
    )
    assert isinstance(result["spearman"], float)
    assert -1.0 <= result["spearman"] <= 1.0
    assert result["n"] == 5
```

**Step 2: Run to verify it fails**

```bash
cd /Users/gaurav/job-search-agent
python3 -m pytest tests/test_autoresearch.py::test_evaluate_returns_float_between_neg1_and_1 -v
```
Expected: `ModuleNotFoundError` or `ImportError` — file doesn't exist yet.

**Step 3: Install scipy if needed**

```bash
pip install scipy
```

**Step 4: Write `autoresearch/evaluate.py`**

```python
"""
autoresearch/evaluate.py — LOCKED evaluation function.
DO NOT MODIFY. All experiments must be compared using this function.

Returns Spearman rank correlation between agent scores and ground truth
on the frozen 30-job test set.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

_BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TESTSET_PATH = os.path.join(_BASE, "testset.json")
DEFAULT_PROMPT_PATH  = os.path.join(_BASE, "scoring_prompt.md")


def evaluate(
    testset_path: str = DEFAULT_TESTSET_PATH,
    prompt_path: str = DEFAULT_PROMPT_PATH,
    cv_skills: str = "",
    cv_summary: str = "",
) -> dict:
    """
    Score every job in the testset with the current prompt and return
    Spearman rank correlation vs ground truth.

    Returns:
        {"spearman": float, "n": int, "scores": list[dict]}
    """
    from scipy.stats import spearmanr
    from agent.llm import call_llm_json

    # Load test set
    with open(testset_path, "r", encoding="utf-8") as f:
        testset = json.load(f)

    # Load current prompt template
    if prompt_path and os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = f.read()
    else:
        template = None  # fallback handled below

    ground_truth = []
    agent_scores  = []
    details       = []

    for job in testset:
        role    = job.get("role", "")
        company = job.get("company", "")
        jd      = (job.get("job_description") or "")[:800]
        gt      = job["ground_truth_score"]

        if template:
            prompt = template.format(
                role=role, company=company, jd=jd,
                cv_skills=cv_skills, cv_summary=cv_summary
            )
        else:
            prompt = (
                f"Score this job 0-100 for this candidate.\n"
                f"Role: {role}\nJD: {jd}\nCV Skills: {cv_skills}\n"
                f'Return JSON: {{"score": <int>, "reason": "<str>"}}'
            )

        try:
            result = call_llm_json(prompt)
            score  = int(result.get("score", 50))
        except Exception as e:
            logger.warning("evaluate: scoring failed for %s @ %s: %s", role, company, e)
            score = 50

        ground_truth.append(gt)
        agent_scores.append(score)
        details.append({
            "job_id":          job.get("job_id", ""),
            "role":            role,
            "company":         company,
            "ground_truth":    gt,
            "agent_score":     score,
        })

    corr, pvalue = spearmanr(ground_truth, agent_scores)
    spearman = float(corr) if not (corr != corr) else 0.0  # nan guard

    logger.info("evaluate: spearman=%.4f (n=%d, p=%.4f)", spearman, len(testset), float(pvalue))
    return {"spearman": spearman, "n": len(testset), "scores": details, "pvalue": float(pvalue)}
```

**Step 5: Run the test**

```bash
python3 -m pytest tests/test_autoresearch.py::test_evaluate_returns_float_between_neg1_and_1 -v
```
Expected: PASS.

**Step 6: Commit**

```bash
git add autoresearch/evaluate.py tests/test_autoresearch.py
git commit -m "feat: add locked evaluate.py — Spearman rank correlation on frozen testset"
```

---

### Task 3: Write the test-set seeder

**Files:**
- Create: `autoresearch/seed.py`

**Step 1: Write `autoresearch/seed.py`**

```python
"""
autoresearch/seed.py — Run ONCE to create testset.json.
Samples 30 diverse jobs from jobs.db, scores them with a high-quality LLM,
and saves the frozen ground truth to testset.json.

Usage:
    python autoresearch/seed.py [--n 30] [--force]
"""
import argparse
import json
import logging
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

_BASE          = os.path.dirname(os.path.abspath(__file__))
TESTSET_PATH   = os.path.join(_BASE, "testset.json")
BASELINE_PATH  = os.path.join(_BASE, "baseline.json")


def _sample_jobs(n: int) -> list:
    """Sample n diverse jobs from the DB (spread across score bands)."""
    from database import get_connection
    conn = get_connection()
    cur  = conn.cursor()

    jobs = []
    # 10 high-scoring, 10 mid, 10 low
    for score_min, score_max in [(70, 100), (40, 70), (0, 40)]:
        cur.execute(
            """SELECT job_id, role, company, job_description, relevance_score
               FROM job_listings
               WHERE relevance_score >= ? AND relevance_score < ?
                 AND (hidden = 0 OR hidden IS NULL)
                 AND job_description IS NOT NULL AND job_description != ''
               ORDER BY RANDOM() LIMIT ?""",
            (score_min, score_max, n // 3)
        )
        jobs.extend([dict(r) for r in cur.fetchall()])

    conn.close()
    random.shuffle(jobs)
    logger.info("Sampled %d jobs from DB", len(jobs))
    return jobs


def _ground_truth_score(job: dict, cv_skills: str, cv_summary: str) -> dict:
    """Score one job with a high-quality LLM call as ground truth."""
    from agent.llm import call_llm_json

    role    = job.get("role", "")
    company = job.get("company", "")
    jd      = (job.get("job_description") or "")[:1000]

    prompt = f"""You are an expert career advisor. Rate how well this job matches the candidate's CV.
Be precise and use the full 0-100 range. Penalise hard mismatches (wrong domain, seniority gap) heavily.

JOB:
Role: {role}
Company: {company}
Description: {jd}

CANDIDATE:
Skills: {cv_skills}
Background: {cv_summary}

Return ONLY valid JSON:
{{"score": <integer 0-100>, "reason": "<2 sentences explaining the rating>"}}"""

    result = call_llm_json(prompt)
    score  = max(0, min(100, int(result.get("score", 50))))
    return {"score": score, "reason": result.get("reason", "")}


def seed(n: int = 30, force: bool = False) -> str:
    """Create testset.json. Returns path to created file."""
    if os.path.exists(TESTSET_PATH) and not force:
        logger.info("testset.json already exists. Use --force to reseed.")
        return TESTSET_PATH

    from analyzer import load_cv_data
    cv_data   = load_cv_data() or {}
    cv_skills = ", ".join((cv_data.get("skills") or [])[:20])
    cv_summary = (cv_data.get("raw_text") or "")[:600]

    if not cv_skills:
        logger.error("No CV data found. Upload your CV first at /cv")
        sys.exit(1)

    jobs   = _sample_jobs(n)
    testset = []

    for i, job in enumerate(jobs, 1):
        logger.info("[%d/%d] Scoring: %s @ %s", i, len(jobs), job["role"], job["company"])
        try:
            gt = _ground_truth_score(job, cv_skills, cv_summary)
        except Exception as e:
            logger.warning("  Failed: %s — skipping", e)
            continue

        testset.append({
            "job_id":             job["job_id"],
            "role":               job["role"],
            "company":            job["company"],
            "job_description":    (job.get("job_description") or "")[:800],
            "db_relevance_score": job.get("relevance_score", 0),
            "ground_truth_score": gt["score"],
            "ground_truth_reason": gt["reason"],
        })

    with open(TESTSET_PATH, "w", encoding="utf-8") as f:
        json.dump(testset, f, indent=2)

    logger.info("Saved %d jobs to %s", len(testset), TESTSET_PATH)
    return TESTSET_PATH


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n",     type=int, default=30)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    seed(args.n, args.force)
```

**Step 2: Verify it can be imported without errors**

```bash
cd /Users/gaurav/job-search-agent
python3 -c "from autoresearch.seed import seed; print('import OK')"
```
Expected: `import OK`

**Step 3: Commit**

```bash
git add autoresearch/seed.py
git commit -m "feat: add seed.py — one-time frozen testset creation"
```

---

### Task 4: Write the improvement loop

**Files:**
- Create: `autoresearch/loop.py`

**Step 1: Write `autoresearch/loop.py`**

```python
"""
autoresearch/loop.py — The self-improvement loop.
Reads scoring_prompt.md + recent history, asks LLM for a hypothesis,
applies it, evaluates, then commits (improved) or resets (worse).

Usage:
    python autoresearch/loop.py [--max-experiments 100] [--dry-run]
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

_BASE         = os.path.dirname(os.path.abspath(__file__))
PROMPT_PATH   = os.path.join(_BASE, "scoring_prompt.md")
TESTSET_PATH  = os.path.join(_BASE, "testset.json")
BASELINE_PATH = os.path.join(_BASE, "baseline.json")
RESULTS_PATH  = os.path.join(_BASE, "results.json")

# Module-level stop flag (set by Flask route to stop the loop)
_stop_event = threading.Event()
_status: dict = {
    "running": False,
    "experiment": 0,
    "best_spearman": None,
    "baseline_spearman": None,
    "last_hypothesis": "",
    "last_outcome": "",
    "last_delta": 0.0,
    "consecutive_failures": 0,
    "log": [],  # last 20 entries
}


def get_status() -> dict:
    return dict(_status)


def stop():
    _stop_event.set()


def _git(args: list, cwd: str = None) -> str:
    cwd = cwd or os.path.dirname(_BASE)
    result = subprocess.run(
        ["git"] + args, cwd=cwd,
        capture_output=True, text=True
    )
    return result.stdout.strip()


def _git_log_summary(n: int = 10) -> str:
    """Return last n autoresearch commits as a readable summary."""
    log = _git(["log", "--oneline", f"-{n}", "--", "autoresearch/scoring_prompt.md"])
    return log or "(no previous experiments)"


def _load_results(n: int = 10) -> list:
    if not os.path.exists(RESULTS_PATH):
        return []
    with open(RESULTS_PATH, "r") as f:
        all_results = json.load(f)
    return all_results[-n:]


def _append_result(entry: dict):
    results = []
    if os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, "r") as f:
            results = json.load(f)
    results.append(entry)
    with open(RESULTS_PATH, "w") as f:
        json.dump(results, f, indent=2)


def _propose_hypothesis(
    current_prompt: str,
    recent_results: list,
    best_spearman: float,
    baseline_spearman: float,
    cv_skills: str,
    cv_summary: str,
) -> dict:
    """Ask the LLM to propose a hypothesis and new prompt. Returns {hypothesis, new_prompt}."""
    from agent.llm import call_llm_json

    history_str = ""
    for r in recent_results[-10:]:
        outcome = "✓ COMMITTED" if r.get("committed") else "✗ RESET"
        history_str += (
            f"  #{r['experiment']}: {outcome} Δ={r['delta']:+.4f} — {r['hypothesis']}\n"
        )

    meta_prompt = f"""You are an expert prompt engineer improving a job-scoring LLM prompt.
Your goal: improve the Spearman rank correlation between the prompt's scores and human ground truth.

CURRENT BEST SPEARMAN: {best_spearman:.4f} (baseline: {baseline_spearman:.4f})

CANDIDATE PROFILE:
Skills: {cv_skills}
Background: {cv_summary[:300]}

RECENT EXPERIMENT HISTORY:
{history_str or "(no history yet — first experiment)"}

CURRENT SCORING PROMPT:
---
{current_prompt}
---

Propose ONE specific, targeted change to improve ranking accuracy.
Think about: scoring criteria weights, how to handle domain mismatches,
how to handle seniority gaps, what signals to prioritise.

Return ONLY valid JSON:
{{
  "hypothesis": "<one sentence describing the change and why it should help>",
  "new_prompt": "<the complete updated prompt with your change applied>"
}}"""

    result = call_llm_json(meta_prompt)
    return result


def run_loop(
    max_experiments: int = 100,
    plateau_patience: int = 20,
    dry_run: bool = False,
):
    """Main autoresearch loop. Runs until stopped or max_experiments reached."""
    global _status
    _stop_event.clear()
    _status["running"] = True
    _status["log"] = []

    from analyzer import load_cv_data
    from autoresearch.evaluate import evaluate

    cv_data   = load_cv_data() or {}
    cv_skills = ", ".join((cv_data.get("skills") or [])[:20])
    cv_summary = (cv_data.get("raw_text") or "")[:600]

    # Establish or load baseline
    if os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH) as f:
            baseline = json.load(f)
        baseline_spearman = baseline["spearman"]
    else:
        logger.info("Computing baseline...")
        baseline = evaluate(TESTSET_PATH, PROMPT_PATH, cv_skills, cv_summary)
        baseline_spearman = baseline["spearman"]
        with open(BASELINE_PATH, "w") as f:
            json.dump({"spearman": baseline_spearman, "timestamp": datetime.now().isoformat()}, f)
        logger.info("Baseline Spearman: %.4f", baseline_spearman)

    best_spearman = baseline_spearman
    _status["baseline_spearman"] = baseline_spearman
    _status["best_spearman"]     = best_spearman
    consecutive_failures = 0
    experiment_num = len(_load_results())

    try:
        for _ in range(max_experiments):
            if _stop_event.is_set():
                logger.info("Loop stopped by user.")
                break

            if consecutive_failures >= plateau_patience:
                logger.info("Plateau detected (%d failures). Stopping.", consecutive_failures)
                break

            experiment_num += 1
            _status["experiment"] = experiment_num
            logger.info("=== Experiment #%d (best=%.4f) ===", experiment_num, best_spearman)

            # Read current prompt
            with open(PROMPT_PATH, "r") as f:
                current_prompt = f.read()

            recent_results = _load_results(10)

            # Propose hypothesis
            try:
                proposal = _propose_hypothesis(
                    current_prompt, recent_results,
                    best_spearman, baseline_spearman,
                    cv_skills, cv_summary
                )
            except Exception as e:
                logger.error("Hypothesis generation failed: %s", e)
                continue

            hypothesis = proposal.get("hypothesis", "")
            new_prompt  = proposal.get("new_prompt", "")

            if not new_prompt or new_prompt == current_prompt:
                logger.warning("LLM returned empty or unchanged prompt, skipping.")
                continue

            _status["last_hypothesis"] = hypothesis
            logger.info("Hypothesis: %s", hypothesis)

            # Apply change
            if not dry_run:
                with open(PROMPT_PATH, "w") as f:
                    f.write(new_prompt)

            # Evaluate
            try:
                result  = evaluate(TESTSET_PATH, PROMPT_PATH, cv_skills, cv_summary)
                new_spearman = result["spearman"]
            except Exception as e:
                logger.error("Evaluation failed: %s", e)
                if not dry_run:
                    _git(["checkout", "autoresearch/scoring_prompt.md"])
                continue

            delta     = new_spearman - best_spearman
            committed = (new_spearman > best_spearman) and not dry_run

            if committed:
                best_spearman = new_spearman
                consecutive_failures = 0
                msg = f"experiment #{experiment_num}: {delta:+.4f} spearman — {hypothesis}"
                _git(["add", "autoresearch/scoring_prompt.md"])
                _git(["commit", "-m", f"autoresearch: {msg}"])
                logger.info("✓ COMMITTED — new best: %.4f", best_spearman)
                _status["last_outcome"] = "committed"
            else:
                consecutive_failures += 1
                if not dry_run:
                    _git(["checkout", "autoresearch/scoring_prompt.md"])
                logger.info("✗ RESET — no improvement (%.4f → %.4f)", best_spearman, new_spearman)
                _status["last_outcome"] = "reset"

            _status["best_spearman"]         = best_spearman
            _status["last_delta"]            = delta
            _status["consecutive_failures"]  = consecutive_failures

            entry = {
                "experiment":  experiment_num,
                "timestamp":   datetime.now().isoformat(),
                "hypothesis":  hypothesis,
                "spearman_before": best_spearman - delta if committed else best_spearman,
                "spearman_after":  new_spearman,
                "delta":       delta,
                "committed":   committed,
            }
            _append_result(entry)
            _status["log"].insert(0, entry)
            _status["log"] = _status["log"][:20]

    finally:
        _status["running"] = False
        logger.info("Loop finished. Best Spearman: %.4f (baseline: %.4f, improvement: %+.4f)",
                    best_spearman, baseline_spearman, best_spearman - baseline_spearman)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-experiments", type=int, default=100)
    parser.add_argument("--plateau-patience", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_loop(args.max_experiments, args.plateau_patience, args.dry_run)
```

**Step 2: Verify import**

```bash
cd /Users/gaurav/job-search-agent
python3 -c "from autoresearch.loop import run_loop, get_status, stop; print('import OK')"
```
Expected: `import OK`

**Step 3: Commit**

```bash
git add autoresearch/loop.py
git commit -m "feat: add autoresearch loop — hypothesis → evaluate → commit or reset"
```

---

### Task 5: Add Flask routes for the UI

**Files:**
- Modify: `app.py` (add 5 routes near the existing agent routes)
- Create: `templates/autoresearch.html`

**Step 1: Add routes to `app.py`**

Find the section near line 1428 where `run_agent_pipeline` routes live. Add the following routes **after** them:

```python
# ── Autoresearch routes ──────────────────────────────────────────────────────

@app.route("/autoresearch")
def autoresearch_page():
    """Autoresearch dashboard page."""
    from autoresearch.loop import get_status
    import json, os
    results_path = os.path.join(BASE_DIR, "autoresearch", "results.json")
    results = []
    if os.path.exists(results_path):
        with open(results_path) as f:
            results = json.load(f)
    baseline_path = os.path.join(BASE_DIR, "autoresearch", "baseline.json")
    baseline = None
    if os.path.exists(baseline_path):
        with open(baseline_path) as f:
            baseline = json.load(f)
    prompt_path = os.path.join(BASE_DIR, "autoresearch", "scoring_prompt.md")
    current_prompt = ""
    if os.path.exists(prompt_path):
        with open(prompt_path) as f:
            current_prompt = f.read()
    return render_template(
        "autoresearch.html",
        status=get_status(),
        results=list(reversed(results[-50:])),
        baseline=baseline,
        current_prompt=current_prompt,
    )


@app.route("/api/autoresearch/start", methods=["POST"])
def autoresearch_start():
    """Start the autoresearch loop in a background thread."""
    import threading
    from autoresearch.loop import run_loop, get_status
    status = get_status()
    if status["running"]:
        return jsonify({"ok": False, "error": "Already running"}), 400
    data = request.get_json(silent=True) or {}
    max_exp = int(data.get("max_experiments", 10))
    t = threading.Thread(target=run_loop, kwargs={"max_experiments": max_exp}, daemon=True)
    t.start()
    return jsonify({"ok": True, "max_experiments": max_exp})


@app.route("/api/autoresearch/stop", methods=["POST"])
def autoresearch_stop():
    """Stop the running loop."""
    from autoresearch.loop import stop
    stop()
    return jsonify({"ok": True})


@app.route("/api/autoresearch/status")
def autoresearch_status():
    """Poll current loop status (used by UI)."""
    from autoresearch.loop import get_status
    return jsonify(get_status())


@app.route("/api/autoresearch/seed", methods=["POST"])
def autoresearch_seed():
    """Run seed.py to create testset.json (one-time setup)."""
    import threading, os
    testset_path = os.path.join(BASE_DIR, "autoresearch", "testset.json")
    force = (request.get_json(silent=True) or {}).get("force", False)
    if os.path.exists(testset_path) and not force:
        return jsonify({"ok": False, "error": "testset.json already exists. Pass force=true to reseed."})
    def _do_seed():
        from autoresearch.seed import seed
        seed(n=30, force=force)
    threading.Thread(target=_do_seed, daemon=True).start()
    return jsonify({"ok": True, "message": "Seeding started in background (takes ~2 min)"})
```

**Step 2: Write `templates/autoresearch.html`**

```html
{% extends "base.html" %}
{% block title %}Autoresearch — Job Agent{% endblock %}

{% block content %}
<div class="mb-8 flex items-start justify-between gap-4 flex-wrap">
  <div>
    <h1 class="text-2xl font-bold tracking-tight" style="color:#1e1b4b;">Autoresearch</h1>
    <p class="mt-1 text-sm font-medium" style="color:#6b7280;">
      Self-improving CV scoring — Karpathy-style autonomous prompt optimization.
    </p>
  </div>

  <!-- Controls -->
  <div class="flex items-center gap-3 flex-shrink-0 flex-wrap">
    {% set testset_exists = current_prompt %}
    <button onclick="seedTestset()"
      class="px-4 py-2 text-sm font-semibold rounded-xl border transition-all"
      style="background:rgba(255,255,255,0.6); border-color:rgba(255,255,255,0.5); color:#374151;"
      id="seed-btn">Seed Test Set</button>

    <button onclick="startLoop(10)"
      class="px-4 py-2 text-white text-sm font-bold rounded-xl transition-all"
      style="background:linear-gradient(135deg,#6366f1,#8b5cf6); box-shadow:0 2px 10px rgba(99,102,241,0.32);"
      id="run-btn">Run 10 Experiments</button>

    <button onclick="startLoop(100)"
      class="px-4 py-2 text-white text-sm font-bold rounded-xl transition-all"
      style="background:linear-gradient(135deg,#059669,#10b981); box-shadow:0 2px 10px rgba(16,185,129,0.30);"
      id="overnight-btn">Run Overnight (100)</button>

    <button onclick="stopLoop()" id="stop-btn"
      class="hidden px-4 py-2 text-sm font-semibold rounded-xl border"
      style="background:rgba(254,226,226,0.7); border-color:rgba(239,68,68,0.3); color:#dc2626;">Stop</button>
  </div>
</div>

<!-- Status bar -->
<div id="status-bar" class="hidden mb-6 px-5 py-3.5 rounded-xl text-sm font-medium"
  style="background:rgba(99,102,241,0.10); border:1px solid rgba(99,102,241,0.20); color:#4338ca; backdrop-filter:blur(12px);">
</div>

<!-- Metric cards -->
<div class="grid grid-cols-2 lg:grid-cols-4 gap-5 mb-8">
  <div class="stat-card">
    <p class="text-xs font-semibold uppercase tracking-wider" style="color:#9ca3af;">Baseline Spearman</p>
    <p class="text-3xl font-bold mt-2" style="color:#1e1b4b;" id="metric-baseline">
      {% if baseline %}{{ "%.4f"|format(baseline.spearman) }}{% else %}—{% endif %}
    </p>
    <p class="text-xs mt-2" style="color:#9ca3af;">initial score before experiments</p>
  </div>
  <div class="stat-card">
    <p class="text-xs font-semibold uppercase tracking-wider" style="color:#9ca3af;">Best Spearman</p>
    <p class="text-3xl font-bold mt-2" style="color:#6366f1;" id="metric-best">
      {% if status.best_spearman %}{{ "%.4f"|format(status.best_spearman) }}{% else %}—{% endif %}
    </p>
    <p class="text-xs mt-2" style="color:#9ca3af;">best achieved so far</p>
  </div>
  <div class="stat-card">
    <p class="text-xs font-semibold uppercase tracking-wider" style="color:#9ca3af;">Experiments</p>
    <p class="text-3xl font-bold mt-2" style="color:#1e1b4b;" id="metric-experiments">{{ results|length }}</p>
    <p class="text-xs mt-2" style="color:#9ca3af;">total iterations run</p>
  </div>
  <div class="stat-card">
    <p class="text-xs font-semibold uppercase tracking-wider" style="color:#9ca3af;">Improvements</p>
    <p class="text-3xl font-bold mt-2" style="color:#059669;" id="metric-improvements">
      {{ results | selectattr("committed") | list | length }}
    </p>
    <p class="text-xs mt-2" style="color:#9ca3af;">commits that beat the baseline</p>
  </div>
</div>

<!-- Experiment log -->
{% if results %}
<div class="glass-panel mb-8">
  <div class="glass-panel-header flex items-center justify-between">
    <h2 class="text-sm font-bold uppercase tracking-wider" style="color:#374151;">Experiment Log</h2>
    <span class="text-xs" style="color:#9ca3af;">newest first</span>
  </div>
  <div class="overflow-x-auto">
    <table class="w-full text-sm">
      <thead>
        <tr style="background:rgba(243,244,246,0.5);">
          <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style="color:#6b7280;">#</th>
          <th class="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider" style="color:#6b7280;">Hypothesis</th>
          <th class="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wider" style="color:#6b7280;">Δ Spearman</th>
          <th class="px-4 py-3 text-center text-xs font-semibold uppercase tracking-wider" style="color:#6b7280;">Outcome</th>
        </tr>
      </thead>
      <tbody>
        {% for r in results %}
        <tr style="border-top:1px solid rgba(255,255,255,0.4);" class="hover:bg-white/20 transition-colors">
          <td class="px-4 py-3 font-mono text-xs" style="color:#9ca3af;">{{ r.experiment }}</td>
          <td class="px-4 py-3 text-sm max-w-md" style="color:#374151;">{{ r.hypothesis }}</td>
          <td class="px-4 py-3 text-right font-bold font-mono text-sm
            {% if r.delta > 0 %}text-emerald-600{% elif r.delta < 0 %}text-rose-500{% else %}text-gray-400{% endif %}">
            {{ "%+.4f"|format(r.delta) }}
          </td>
          <td class="px-4 py-3 text-center">
            {% if r.committed %}
            <span class="px-2 py-1 rounded-md text-xs font-bold" style="background:rgba(16,185,129,0.1); color:#059669;">✓ committed</span>
            {% else %}
            <span class="px-2 py-1 rounded-md text-xs font-bold" style="background:rgba(243,244,246,0.7); color:#9ca3af;">✗ reset</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endif %}

<!-- Current prompt viewer -->
{% if current_prompt %}
<div class="glass-panel">
  <div class="glass-panel-header">
    <h2 class="text-sm font-bold uppercase tracking-wider" style="color:#374151;">Current Scoring Prompt</h2>
  </div>
  <div class="p-6">
    <pre class="text-xs leading-relaxed whitespace-pre-wrap rounded-xl p-4"
      style="background:rgba(248,248,255,0.6); color:#374151; font-family:ui-monospace,monospace;">{{ current_prompt }}</pre>
  </div>
</div>
{% else %}
<div class="glass-panel p-8 text-center">
  <p class="text-sm font-medium mb-4" style="color:#6b7280;">No test set yet. Click <strong>Seed Test Set</strong> to get started.</p>
</div>
{% endif %}

<script>
let pollInterval = null;

function showStatus(msg, color) {
  const bar = document.getElementById('status-bar');
  bar.textContent = msg;
  bar.style.display = 'block';
  bar.style.color = color || '#4338ca';
}

function seedTestset() {
  document.getElementById('seed-btn').textContent = 'Seeding…';
  fetch('/api/autoresearch/seed', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'})
    .then(r => r.json()).then(d => {
      showStatus(d.ok ? '✓ Seeding started — takes ~2 min. Refresh when done.' : ('Error: ' + d.error));
      document.getElementById('seed-btn').textContent = 'Seed Test Set';
    });
}

function startLoop(n) {
  fetch('/api/autoresearch/start', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({max_experiments: n})
  }).then(r => r.json()).then(d => {
    if (!d.ok) { showStatus('Error: ' + d.error, '#dc2626'); return; }
    document.getElementById('stop-btn').classList.remove('hidden');
    showStatus('Running experiment loop (' + n + ' experiments)…');
    pollInterval = setInterval(pollStatus, 3000);
  });
}

function stopLoop() {
  fetch('/api/autoresearch/stop', {method:'POST'})
    .then(() => { clearInterval(pollInterval); showStatus('Stopped.'); });
}

function pollStatus() {
  fetch('/api/autoresearch/status').then(r => r.json()).then(s => {
    if (s.best_spearman) document.getElementById('metric-best').textContent = s.best_spearman.toFixed(4);
    document.getElementById('metric-experiments').textContent = s.experiment;
    if (s.last_hypothesis) {
      const outcome = s.last_outcome === 'committed' ? '✓' : '✗';
      const delta   = s.last_delta >= 0 ? '+' + s.last_delta.toFixed(4) : s.last_delta.toFixed(4);
      showStatus(`Exp #${s.experiment} ${outcome} ${delta} — ${s.last_hypothesis}`);
    }
    if (!s.running) {
      clearInterval(pollInterval);
      document.getElementById('stop-btn').classList.add('hidden');
      showStatus('Loop finished. Best Spearman: ' + (s.best_spearman || '—'));
      setTimeout(() => location.reload(), 2000);
    }
  });
}
</script>
{% endblock %}
```

**Step 3: Verify the page loads**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:5001/autoresearch
```
Expected: `200`

**Step 4: Commit**

```bash
git add app.py templates/autoresearch.html
git commit -m "feat: add autoresearch Flask routes and dashboard UI"
```

---

### Task 6: Add sidebar link to Autoresearch page

**Files:**
- Modify: `templates/base.html`

**Step 1: Add nav link in the Tools section of the sidebar**

In `base.html`, find the Tools section nav group (around line 136). Add after the "Run Scraper" link:

```html
<a href="{{ url_for('autoresearch_page') }}" class="sidebar-link {% if request.endpoint == 'autoresearch_page' %}active{% endif %}">
  <svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
      d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
  </svg>
  Autoresearch
</a>
```

Also add to mobile nav drawer (Tools section, same pattern).

**Step 2: Verify sidebar link appears**

```bash
curl -s http://localhost:5001/ | grep -i "autoresearch"
```
Expected: contains `autoresearch`.

**Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat: add Autoresearch link to sidebar"
```

---

### Task 7: End-to-end smoke test

**Step 1: Check all imports are clean**

```bash
cd /Users/gaurav/job-search-agent
python3 -c "
import autoresearch.evaluate
import autoresearch.seed
import autoresearch.loop
print('All imports OK')
"
```
Expected: `All imports OK`

**Step 2: Run all tests**

```bash
python3 -m pytest tests/ -v --tb=short
```
Expected: All existing tests pass + new autoresearch test passes.

**Step 3: Verify the full UI flow manually**

1. Open `http://localhost:5001/autoresearch`
2. Confirm the page loads with metric cards
3. Confirm "Seed Test Set" button hits `/api/autoresearch/seed`
4. Confirm "Run 10 Experiments" starts polling `/api/autoresearch/status`

**Step 4: Final commit**

```bash
git add .
git commit -m "feat: autoresearch — complete self-improving CV scoring loop"
```

---

## Usage After Setup

```bash
# 1. Seed the test set (one-time, takes ~2 min)
python autoresearch/seed.py

# 2. Run a quick test (1 experiment, dry-run)
python autoresearch/loop.py --max-experiments 1 --dry-run

# 3. Run overnight
python autoresearch/loop.py --max-experiments 100

# 4. Or use the web UI at http://localhost:5001/autoresearch
```
