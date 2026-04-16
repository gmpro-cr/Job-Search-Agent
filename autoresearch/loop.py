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
    s = dict(_status)
    s["log"] = list(_status["log"])  # copy so Flask read doesn't race with loop write
    return s


def stop():
    _stop_event.set()
    _status["running"] = False


def _git(args: list, cwd: str = None) -> str:
    cwd = cwd or os.path.dirname(_BASE)
    result = subprocess.run(
        ["git"] + args, cwd=cwd,
        capture_output=True, text=True
    )
    if result.returncode != 0:
        logger.warning("git %s failed: %s", " ".join(args), result.stderr.strip())
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
    cv_summary = (cv_data.get("raw_text") or "")[:200]

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
    experiment_num = len(_load_results(n=9999))

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
                consecutive_failures += 1
                _status["consecutive_failures"] = consecutive_failures
                continue

            hypothesis = proposal.get("hypothesis", "")
            new_prompt  = proposal.get("new_prompt", "")

            if not new_prompt or new_prompt == current_prompt:
                logger.warning("LLM returned empty or unchanged prompt, skipping.")
                consecutive_failures += 1
                _status["consecutive_failures"] = consecutive_failures
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
                consecutive_failures += 1
                _status["consecutive_failures"] = consecutive_failures
                continue

            if new_spearman is None:
                logger.error("evaluate() returned None spearman, skipping.")
                if not dry_run:
                    _git(["checkout", "autoresearch/scoring_prompt.md"])
                consecutive_failures += 1
                _status["consecutive_failures"] = consecutive_failures
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
