# tests/test_autoresearch.py
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

def test_evaluate_returns_float_between_neg1_and_1(tmp_path, monkeypatch):
    """evaluate() returns Spearman correlation in [-1, 1]."""
    testset = [
        {"job_id": f"j{i}", "role": "PM", "company": "Acme",
         "job_description": "Build products", "ground_truth_score": i * 10}
        for i in range(1, 6)
    ]
    testset_path = tmp_path / "testset.json"
    testset_path.write_text(json.dumps(testset))

    scores = iter([55, 44, 33, 22, 11])
    monkeypatch.setattr(
        "autoresearch.evaluate.call_llm_json",
        lambda prompt: {"score": next(scores), "reason": "test"}
    )

    from autoresearch.evaluate import evaluate
    result = evaluate(
        testset_path=str(testset_path),
        prompt_path=None,
        cv_skills="SQL", cv_summary="Banker"
    )
    assert isinstance(result["spearman"], float)
    assert -1.0 <= result["spearman"] <= 1.0
    assert result["n"] == 5
