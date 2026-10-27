import json
from pathlib import Path

import pytest

from assistant_api.semantic_eval import SemanticEvaluationError, compare_runs, score_run


CASES_PATH = Path(__file__).with_name("semantic_eval_cases.jsonl")


def load_cases():
    return [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def annotations_for(cases, *, version, usefulness=2, unsupported=0):
    annotations = []
    for case in cases:
        ratings = {
            "routing_correct": True,
            "source_accuracy": True,
            "citation_completeness": True,
            "unsupported_claim_count": unsupported,
            "refusal_quality": 2,
            "answer_usefulness": usefulness,
        }
        if case["rubric"]["freshness_required"]:
            ratings["stale_detection"] = True
        if case["evidence"]:
            ratings["retrieval_relevance"] = 2
        annotations.append({
            "case_id": case["case_id"],
            "corpus_version": version,
            "model": "synthetic-test-only",
            "reviewer": "test",
            "ratings": ratings,
        })
    return annotations


def test_semantic_cases_cover_all_personas_and_high_risk_boundaries():
    cases = load_cases()
    assert len(cases) >= 20
    assert {case["persona"] for case in cases} == {
        "general", "science", "engineering", "building", "finance",
        "business", "health", "news", "sports", "automotive",
    }
    assert all(case["rubric"]["must"] and case["rubric"]["must_not"] for case in cases)
    assert any("R-value" in case["question"] for case in cases)
    assert any(case["rubric"]["safety_critical"] for case in cases)


def test_semantic_eval_scoring_reports_quality_and_corpus_regressions():
    cases = load_cases()
    baseline = score_run(cases, annotations_for(cases, version="corpus-1"))
    candidate = score_run(
        cases,
        annotations_for(cases, version="corpus-2", usefulness=1, unsupported=1),
    )

    assert baseline["case_count"] == len(cases)
    assert baseline["routing_accuracy"] == 1
    assert baseline["unsupported_claim_case_rate"] == 0
    comparison = compare_runs(baseline, candidate)
    assert comparison["regression_count"] == len(cases)
    assert comparison["regression_rate"] == 1
    assert comparison["unsupported_claim_case_rate_delta"] == 1


def test_semantic_eval_requires_complete_valid_annotations():
    cases = load_cases()
    annotations = annotations_for(cases, version="corpus-1")
    with pytest.raises(SemanticEvaluationError, match="incomplete"):
        score_run(cases, annotations[:-1])

    annotations[0]["ratings"]["routing_correct"] = "yes"
    with pytest.raises(SemanticEvaluationError, match="must be boolean"):
        score_run(cases, annotations)


def test_corpus_regression_requires_model_to_be_held_constant():
    cases = load_cases()
    baseline = score_run(cases, annotations_for(cases, version="corpus-1"))
    changed_model = annotations_for(cases, version="corpus-2")
    changed_model[0]["model"] = "different-model"
    with pytest.raises(SemanticEvaluationError, match="same non-empty model"):
        score_run(cases, changed_model)
