import json
from pathlib import Path


def test_source_candidates_are_reference_only_and_well_formed():
    path = Path(__file__).resolve().parents[1] / "knowledge" / "source_candidates.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))

    assert catalog["version"] == 1
    assert catalog["policy"]["default_status"] == "candidate_unverified"
    assert catalog["policy"]["fetching_enabled"] is False
    assert len(catalog["policy"]["required_before_integration"]) >= 3
    candidates = catalog["candidates"]
    identities = [candidate["id"] for candidate in candidates]
    assert len(identities) == len(set(identities))
    assert candidates
    for candidate in candidates:
        assert candidate["publisher"] and candidate["personas"] and candidate["use"]
        assert candidate["url"] is None or candidate["url"].startswith("https://")
