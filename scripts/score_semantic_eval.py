"""Score human-labelled persona answers and compare corpus revisions."""

import argparse
import json
from pathlib import Path

from assistant_api.semantic_eval import compare_runs, score_run


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSONL record") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_number}: each record must be a JSON object")
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True,
                        help="Human ratings JSONL for one corpus/model run.")
    parser.add_argument("--baseline", type=Path,
                        help="Optional prior-run ratings JSONL for regression comparison.")
    parser.add_argument(
        "--cases", type=Path,
        default=Path(__file__).resolve().parents[1] / "tests" / "semantic_eval_cases.jsonl",
    )
    args = parser.parse_args()
    try:
        cases = read_jsonl(args.cases)
        annotations = read_jsonl(args.annotations)
        result = {"candidate": score_run(cases, annotations)}
        if args.baseline:
            baseline = score_run(cases, read_jsonl(args.baseline))
            result["comparison"] = compare_runs(baseline, result["candidate"])
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Evaluation scoring failed: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
