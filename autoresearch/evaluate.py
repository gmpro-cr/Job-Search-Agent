"""
autoresearch/evaluate.py — LOCKED evaluation function.
DO NOT MODIFY. All experiments must be compared using this function.

Returns Spearman rank correlation between agent scores and ground truth
on the frozen 30-job test set.
"""
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent.llm import call_llm_json  # noqa: E402 — imported here for monkeypatching

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
        {"spearman": float, "n": int, "scores": list[dict], "pvalue": float}
    """
    from scipy.stats import spearmanr

    with open(testset_path, "r", encoding="utf-8") as f:
        testset = json.load(f)[:15]  # use first 15 jobs — enough for Spearman, halves eval time

    if prompt_path and os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = f.read()
    else:
        template = None

    def _score_job(job):
        role    = job.get("role", "")
        company = job.get("company", "")
        jd      = (job.get("job_description") or "")[:300]
        gt      = job["ground_truth_score"]

        if template:
            # Use manual replace instead of .format() — the template contains literal
            # JSON braces like {"score": ...} which confuse str.format()
            prompt = template
            for k, v in [("role", role), ("company", company), ("jd", jd),
                         ("cv_skills", cv_skills), ("cv_summary", cv_summary)]:
                prompt = prompt.replace("{" + k + "}", str(v))
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

        return {
            "job_id":       job.get("job_id", ""),
            "role":         role,
            "company":      company,
            "ground_truth": gt,
            "agent_score":  score,
        }

    # Score jobs sequentially (Ollama doesn't parallelize — one GPU at a time)
    details = [None] * len(testset)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future_to_idx = {executor.submit(_score_job, job): i for i, job in enumerate(testset)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            details[idx] = future.result()

    ground_truth = [d["ground_truth"] for d in details]
    agent_scores  = [d["agent_score"]  for d in details]

    corr, pvalue = spearmanr(ground_truth, agent_scores)
    spearman = float(corr) if corr == corr else 0.0  # nan guard

    logger.info("evaluate: spearman=%.4f (n=%d, p=%.4f)", spearman, len(testset), float(pvalue))
    return {"spearman": spearman, "n": len(testset), "scores": details, "pvalue": float(pvalue)}
