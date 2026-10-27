import json

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant_api.config import Settings
from assistant_api.finance import api as finance_api
from assistant_api.finance import research
from assistant_api.main import create_app


AUTH = {"Authorization": "Bearer test-admin-token-for-local-test-only"}


class Ollama:
    def __init__(self):
        self.loaded = ["daily"]

    def __call__(self, request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [
                {"name": "daily"}, {"name": "research"}, {"name": "llama3.2:latest"},
            ]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": name} for name in self.loaded]})
        if request.url.path == "/api/generate":
            payload = json.loads(request.content)
            if payload.get("keep_alive") == 0:
                self.loaded = [name for name in self.loaded if name != payload["model"]]
            else:
                self.loaded = [payload["model"]]
            return httpx.Response(200, json={"done": True})
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "test answer"}})
        raise AssertionError(request.url)


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        tmp_path, "http://ollama", "daily", "research",
        "test-admin-token-for-local-test-only", False,
    )
    with TestClient(
        create_app(settings, ollama_transport=httpx.MockTransport(Ollama())),
        client=("127.0.0.1", 12345),
    ) as test_client:
        yield test_client


def test_research_job_is_admin_protected_and_runs_one_mocked_instrument(client, tmp_path, monkeypatch):
    called = {}

    def fake_run(instrument, asset_type, job_dir, data_dir, allow_network):
        called.update(
            instrument=instrument,
            asset_type=asset_type,
            data_dir=data_dir,
            allow_network=allow_network,
        )
        (job_dir / "report.md").write_text("# Test report\n", encoding="utf-8")
        research._atomic_json(job_dir / "status.json", {
            "instrument": instrument,
            "status": "completed",
            "progress_percent": 100,
            "stage": "complete",
            "steps": [],
            "issues": [],
            "started_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:01+00:00",
            "finished_at": "2025-01-01T00:00:01+00:00",
            "report": "report.md",
        })
        return 0

    monkeypatch.setattr(research, "run_research", fake_run)
    assert client.post("/api/finance/research-jobs", json={"instrument": "NCK"}).status_code == 401

    response = client.post(
        "/api/finance/research-jobs", headers=AUTH,
        json={"instrument": "NCK"},
    )
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued"
    assert job["external_feeds_enabled"] is False
    assert called == {
        "instrument": "NCK", "asset_type": "auto",
        "data_dir": tmp_path, "allow_network": False,
    }

    status = client.get(job["status_url"], headers=AUTH)
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    report = client.get(job["report_url"], headers=AUTH)
    assert report.status_code == 200
    assert report.text == "# Test report\n"
    assert client.get(job["status_url"]).status_code == 401


def test_research_job_rejects_bad_instrument_and_caps_concurrency(client, monkeypatch):
    invalid = client.post(
        "/api/finance/research-jobs", headers=AUTH,
        json={"instrument": "NCK,CSL"},
    )
    assert invalid.status_code == 422

    with finance_api.ACTIVE_RESEARCH_JOBS_LOCK:
        finance_api.ACTIVE_RESEARCH_JOBS.add("existing-job")
    try:
        busy = client.post(
            "/api/finance/research-jobs", headers=AUTH,
            json={"instrument": "ASX:NCK"},
        )
        assert busy.status_code == 429
    finally:
        with finance_api.ACTIVE_RESEARCH_JOBS_LOCK:
            finance_api.ACTIVE_RESEARCH_JOBS.discard("existing-job")

    assert client.get("/api/finance/research-jobs/" + "a" * 32, headers=AUTH).status_code == 404


def test_batch_download_and_local_model_analysis_complete_with_status(client, tmp_path, monkeypatch):
    executed = []

    def fake_research(instrument, asset_type, job_dir, data_dir, allow_network):
        executed.append((instrument, allow_network))
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "report.md").write_text(f"# Evidence for {instrument}\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(research, "run_research", fake_research)
    response = client.post(
        "/api/finance/research-batches", headers=AUTH,
        json={
            "model": "llama3.2:latest",
            "instruments": [
                {"instrument": "NCK", "asset_type": "auto"},
                {"instrument": "NCK", "asset_type": "auto"},
                {"instrument": "CSL", "asset_type": "auto"},
            ],
        },
    )
    assert response.status_code == 202
    job = response.json()
    assert job["unique_instruments"] == 2
    assert job["duplicates_skipped"] == 1
    assert job["model"] == "llama3.2:latest"

    status = client.get(job["status_url"], headers=AUTH)
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["completed"] == 2
    assert status.json()["failed"] == 0
    assert executed == [("NCK", False), ("CSL", False)]
    analysis = client.get(
        f"/api/finance/research-batches/{job['batch_id']}/items/1/analysis",
        headers=AUTH,
    )
    assert analysis.status_code == 200
    assert "Evidence-only review" not in analysis.text
    assert "test answer" in analysis.text


def test_batch_preflight_rejects_missing_local_model_without_starting(client):
    response = client.post(
        "/api/finance/research-batches", headers=AUTH,
        json={"model": "not-installed", "instruments": [{"instrument": "NCK"}]},
    )
    assert response.status_code == 422
    assert "not installed" in response.json()["detail"]


def test_batch_resume_keeps_finished_company_and_retries_failed_company(client, tmp_path, monkeypatch):
    attempts = {}

    def fake_research(instrument, asset_type, job_dir, data_dir, allow_network):
        attempts[instrument] = attempts.get(instrument, 0) + 1
        job_dir.mkdir(parents=True, exist_ok=True)
        if instrument == "CSL" and attempts[instrument] == 1:
            research._atomic_json(job_dir / "status.json", {
                "issues": [{"severity": "error", "message": "Temporary source failure"}],
            })
            return 1
        (job_dir / "report.md").write_text(f"# Evidence for {instrument}\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(research, "run_research", fake_research)
    started = client.post(
        "/api/finance/research-batches", headers=AUTH,
        json={
            "model": "llama3.2:latest",
            "instruments": [{"instrument": "NCK"}, {"instrument": "CSL"}],
        },
    )
    batch_id = started.json()["batch_id"]
    first = client.get(f"/api/finance/research-batches/{batch_id}", headers=AUTH).json()
    assert first["status"] == "completed_with_errors"
    assert first["completed"] == 1 and first["failed"] == 1

    resumed = client.post(
        f"/api/finance/research-batches/{batch_id}/resume", headers=AUTH, json={},
    )
    assert resumed.status_code == 202
    final = client.get(f"/api/finance/research-batches/{batch_id}", headers=AUTH).json()
    assert final["status"] == "completed"
    assert final["completed"] == 2 and final["failed"] == 0
    assert attempts == {"NCK": 1, "CSL": 2}
