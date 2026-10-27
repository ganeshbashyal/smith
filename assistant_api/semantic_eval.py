"""Human-reviewed scoring for the versioned semantic persona evaluation set."""


class SemanticEvaluationError(ValueError):
    pass


BOOLEAN_RATINGS = ("routing_correct", "source_accuracy", "citation_completeness")
SCALE_RATINGS = ("refusal_quality", "answer_usefulness", "retrieval_relevance")


def score_run(cases: list[dict], annotations: list[dict]) -> dict:
    cases_by_id = {case["case_id"]: case for case in cases}
    if not cases_by_id or len(cases_by_id) != len(cases):
        raise SemanticEvaluationError("Evaluation case IDs must be unique")
    annotations_by_id = {}
    for annotation in annotations:
        identity = annotation.get("case_id")
        if identity not in cases_by_id or identity in annotations_by_id:
            raise SemanticEvaluationError(f"Unknown or duplicate annotation case: {identity}")
        annotations_by_id[identity] = annotation
    missing = set(cases_by_id) - set(annotations_by_id)
    if missing:
        raise SemanticEvaluationError(f"Annotations are incomplete: {', '.join(sorted(missing))}")

    versions = {entry.get("corpus_version") for entry in annotations}
    if len(versions) != 1 or not next(iter(versions)):
        raise SemanticEvaluationError("Every annotation must identify the same non-empty corpus_version")
    models = {entry.get("model") for entry in annotations}
    if len(models) != 1 or not next(iter(models)):
        raise SemanticEvaluationError("Every annotation in a run must use the same non-empty model")

    per_case = []
    for identity, case in cases_by_id.items():
        annotation = annotations_by_id[identity]
        if not annotation.get("reviewer") or not annotation.get("model"):
            raise SemanticEvaluationError(f"{identity}: reviewer and model are required")
        ratings = annotation.get("ratings")
        if not isinstance(ratings, dict):
            raise SemanticEvaluationError(f"{identity}: ratings must be an object")
        required = set(BOOLEAN_RATINGS) | {"unsupported_claim_count", "refusal_quality", "answer_usefulness"}
        if case["rubric"]["freshness_required"]:
            required.add("stale_detection")
        if case["evidence"]:
            required.add("retrieval_relevance")
        missing_ratings = required - ratings.keys()
        if missing_ratings:
            raise SemanticEvaluationError(
                f"{identity}: missing ratings: {', '.join(sorted(missing_ratings))}"
            )
        for name in BOOLEAN_RATINGS + (("stale_detection",) if case["rubric"]["freshness_required"] else ()):
            if type(ratings[name]) is not bool:
                raise SemanticEvaluationError(f"{identity}: {name} must be boolean")
        for name in SCALE_RATINGS:
            if name in required and (type(ratings[name]) is not int or ratings[name] not in (0, 1, 2)):
                raise SemanticEvaluationError(f"{identity}: {name} must be an integer from 0 to 2")
        count = ratings["unsupported_claim_count"]
        if type(count) is not int or count < 0:
            raise SemanticEvaluationError(f"{identity}: unsupported_claim_count must be a non-negative integer")

        normalized = [
            int(ratings["routing_correct"]),
            int(ratings["source_accuracy"]),
            int(ratings["citation_completeness"]),
            int(count == 0),
            ratings["refusal_quality"] / 2,
            ratings["answer_usefulness"] / 2,
        ]
        if case["rubric"]["freshness_required"]:
            normalized.append(int(ratings["stale_detection"]))
        if case["evidence"]:
            normalized.append(ratings["retrieval_relevance"] / 2)
        per_case.append({
            "case_id": identity,
            "score": sum(normalized) / len(normalized),
            "unsupported_claim_count": count,
        })

    all_ratings = [annotations_by_id[case["case_id"]]["ratings"] for case in cases]
    freshness_ratings = [
        annotation["ratings"]["stale_detection"]
        for case, annotation in ((case, annotations_by_id[case["case_id"]]) for case in cases)
        if case["rubric"]["freshness_required"]
    ]
    retrieval_ratings = [
        annotation["ratings"]["retrieval_relevance"]
        for case, annotation in ((case, annotations_by_id[case["case_id"]]) for case in cases)
        if case["evidence"]
    ]
    unsupported_case_count = sum(item["unsupported_claim_count"] > 0 for item in per_case)
    return {
        "corpus_version": next(iter(versions)),
        "model": next(iter(models)),
        "case_count": len(cases),
        "routing_accuracy": sum(item["routing_correct"] for item in all_ratings) / len(cases),
        "source_accuracy": sum(item["source_accuracy"] for item in all_ratings) / len(cases),
        "citation_completeness": sum(item["citation_completeness"] for item in all_ratings) / len(cases),
        "stale_detection_accuracy": (
            sum(freshness_ratings) / len(freshness_ratings) if freshness_ratings else None
        ),
        "unsupported_claim_case_rate": unsupported_case_count / len(cases),
        "retrieval_relevance_average": (
            sum(retrieval_ratings) / len(retrieval_ratings) if retrieval_ratings else None
        ),
        "refusal_quality_average": sum(item["refusal_quality"] for item in all_ratings) / len(cases),
        "answer_usefulness_average": sum(item["answer_usefulness"] for item in all_ratings) / len(cases),
        "answer_quality_average": sum(item["score"] for item in per_case) / len(per_case),
        "per_case": sorted(per_case, key=lambda item: item["case_id"]),
    }


def compare_runs(baseline: dict, candidate: dict) -> dict:
    if baseline["corpus_version"] == candidate["corpus_version"]:
        raise SemanticEvaluationError("Baseline and candidate corpus versions must differ")
    if baseline["model"] != candidate["model"]:
        raise SemanticEvaluationError("Use the same model for corpus regression comparisons")
    baseline_scores = {item["case_id"]: item["score"] for item in baseline["per_case"]}
    candidate_scores = {item["case_id"]: item["score"] for item in candidate["per_case"]}
    paired = baseline_scores.keys() & candidate_scores.keys()
    if not paired or baseline_scores.keys() != candidate_scores.keys():
        raise SemanticEvaluationError("Baseline and candidate runs must contain the same evaluation cases")
    regressions = sorted(
        identity for identity in paired
        if candidate_scores[identity] < baseline_scores[identity]
    )
    return {
        "baseline_corpus_version": baseline["corpus_version"],
        "candidate_corpus_version": candidate["corpus_version"],
        "model": candidate["model"],
        "paired_case_count": len(paired),
        "regression_count": len(regressions),
        "regression_rate": len(regressions) / len(paired),
        "regressed_cases": regressions,
        "answer_quality_delta": candidate["answer_quality_average"] - baseline["answer_quality_average"],
        "unsupported_claim_case_rate_delta": (
            candidate["unsupported_claim_case_rate"] - baseline["unsupported_claim_case_rate"]
        ),
    }
