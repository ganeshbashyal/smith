import json
import asyncio

from assistant_api.finance import research


def test_select_method_defaults_bare_symbols_to_provisional_asx():
    bare = research.select_method("nck")
    explicit = research.select_method("ASX:NCK")
    gold = research.select_method("XAUUSD")
    assert bare["source_symbol"] == "NCK.AX"
    assert bare["classification"] == "provisional"
    assert explicit["classification"] == "identified"
    assert gold["asset_type"] == "commodity"
    assert gold["source_symbol"] == "XAUUSD=X"


def test_select_method_requires_one_valid_instrument():
    for instrument in ("", "NCK,CSL", "NCK\nCSL", "../NCK"):
        try:
            research.select_method(instrument)
        except ValueError:
            continue
        raise AssertionError(f"unexpectedly accepted {instrument!r}")


def test_report_parser_extracts_five_year_history(monkeypatch):
    class Page:
        def extract_text(self):
            return (
                "Past five years: 2022 2023 2024 2025 2026 "
                "Revenue 441.0 507.7 468.2 495.3 516.7 "
                "Net profit after tax 74.9 101.1 80.6 57.7 75.7 "
                "Share price at 30 June ($) 8.26 9.1 1 13.81 18.21 16.32 18.6"
            )

    class Reader:
        is_encrypted = False
        pages = [Page()]

        def __init__(self, _):
            pass

    monkeypatch.setattr(research, "PdfReader", Reader)
    history = research.parse_report_history(b"mock PDF bytes")
    assert history[2022]["revenue_m"] == 441.0
    assert history[2026]["npat_m"] == 75.7
    assert history[2022]["npat_m"] == 74.9
    assert history[2026]["share_price_fy_end"] == 16.32


def test_rba_financial_year_end_keeps_missing_years_missing():
    observations = [
        (research.date(2023, 6, 29), 3.85),
        (research.date(2023, 6, 30), 4.1),
        (research.date(2024, 6, 28), 4.35),
    ]
    assert research.financial_year_end_series(observations, [2022, 2023, 2024]) == {
        2023: 4.1, 2024: 4.35,
    }


def test_offline_job_writes_report_and_explicit_missing_source_issue(tmp_path):
    job_dir = tmp_path / "jobs" / "nck"
    result = research.run_research("NCK", "auto", job_dir, tmp_path / "data", allow_network=False)
    status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    report = (job_dir / "report.md").read_text(encoding="utf-8")
    assert result == 0
    assert status["status"] == "completed"
    assert status["progress_percent"] == 100
    assert (job_dir / "NCK.AX-history.csv").exists() is False
    assert "No issuer-source manifest found" in report
    assert "not zero" in report
    assert "External feeds are disabled" in report


def test_market_chart_escapes_instrument_text(tmp_path):
    path = tmp_path / "chart.svg"
    assert research._market_svg(path, "<NCK & CSL>", {
        "currency": "AUD",
        "bars": [
            {"date": "2025-01-01", "close": 1.0},
            {"date": "2025-01-02", "close": 1.5},
        ],
    })
    svg = path.read_text(encoding="utf-8")
    assert "&lt;NCK &amp; CSL&gt;" in svg
    assert "<NCK & CSL>" not in svg


def test_market_statistics_describes_price_only_data():
    bars = [
        {"date": f"2025-01-{index:02d}", "close": close, "volume": 100 + index}
        for index, close in enumerate((10.0, 9.0, 11.0, 12.0), 1)
    ]
    result = research.market_statistics({"bars": bars})
    assert result["status"] == "available"
    assert result["observations"] == 4
    assert result["maximum_close_drawdown_pct_observed"] == -10.0
    assert result["average_reported_volume_20d"] == 102.5


def test_market_statistics_includes_ten_year_return_when_available():
    bars = [
        {"date": "2016-01-01", "close": 1.0},
        *({"date": "2020-01-01", "close": 2.0} for _ in range(2519)),
        {"date": "2026-01-01", "close": 10.0},
    ]
    result = research.market_statistics({"bars": bars})
    assert result["period_change_pct"]["10_year_pct"] == 900.0


def test_yahoo_history_requests_ten_year_daily_range(monkeypatch):
    seen = []
    timestamp = int(research.datetime(2026, 1, 1, tzinfo=research.timezone.utc).timestamp())
    payload = {
        "chart": {"result": [{
            "meta": {"currency": "AUD", "exchangeTimezoneName": "Australia/Sydney"},
            "timestamp": [timestamp],
            "indicators": {
                "quote": [{"open": [1], "high": [1], "low": [1], "close": [1], "volume": [10]}],
                "adjclose": [{"adjclose": [1]}],
            },
        }]},
    }

    def fake_fetch(url, limit):
        seen.append(url)
        return json.dumps(payload).encode()

    monkeypatch.setattr(research, "fetch_bytes", fake_fetch)
    result = research._fetch_yahoo_history("CSL.AX")
    assert "range=10y" in seen[0]
    assert result["status"] == "ok"


def test_batch_records_each_outcome_and_continues_after_failures(tmp_path, monkeypatch):
    executed = []

    def fake_research(instrument, asset_type, job_dir, data_dir, allow_network):
        executed.append(instrument)
        job_dir.mkdir(parents=True, exist_ok=True)
        if instrument == "FAIL":
            research._atomic_json(job_dir / "status.json", {
                "issues": [{"severity": "error", "message": "No price data"}],
            })
            return 1
        (job_dir / "report.md").write_text(f"# {instrument}\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(research, "run_research", fake_research)
    generated = []

    async def analyze(instrument, report, model):
        generated.append(instrument)
        if instrument == "MODELFAIL":
            raise RuntimeError("local model unavailable for this item")
        return "Evidence-only review.", model

    result = asyncio.run(research.run_batch(
        [{"instrument": "FAIL"}, {"instrument": "MODELFAIL"}, {"instrument": "OK"}],
        tmp_path / "batch", tmp_path / "data",
        allow_network=False, model="llama3.2:latest", analyze=analyze,
    ))
    assert result["status"] == "completed_with_errors"
    assert result["total"] == 3
    assert result["completed"] == 1
    assert result["failed"] == 2
    assert executed == ["FAIL", "MODELFAIL", "OK"]
    assert generated == ["MODELFAIL", "OK"]
    assert result["items"][2]["analysis"] == "03-OK/analysis.md"
    assert (tmp_path / "batch" / "03-OK" / "analysis.md").exists()


def test_instrument_list_loader_deduplicates_but_keeps_list_membership(tmp_path):
    path = tmp_path / "symbols.json"
    path.write_text(json.dumps({
        "lists": {"Watchlist": ["NCK", "DRO"], "Holdings": ["DRO", "CSL"]},
    }), encoding="utf-8")
    items = research.load_instruments_file(path)
    assert [item["instrument"] for item in items] == ["NCK", "DRO", "CSL"]
    assert items[1]["list_names"] == ["Watchlist", "Holdings"]


def test_instrument_list_loader_accepts_asx200_batch(tmp_path):
    path = tmp_path / "symbols.json"
    path.write_text(json.dumps({
        "instruments": [f"ASX:AB{i:02d}" for i in range(200)],
    }), encoding="utf-8")
    items = research.load_instruments_file(path)
    assert len(items) == 200
    assert items[-1]["instrument"] == "ASX:AB199"


def test_instrument_list_loader_rejects_more_than_asx200_batch(tmp_path):
    path = tmp_path / "symbols.json"
    path.write_text(json.dumps({
        "instruments": [f"ASX:AB{i:03d}" for i in range(201)],
    }), encoding="utf-8")
    try:
        research.load_instruments_file(path)
    except research.ResearchError as exc:
        assert "limited to 200" in str(exc)
    else:
        raise AssertionError("expected oversized batch to be rejected")


def test_batch_resume_skips_completed_items_and_retries_failures(tmp_path, monkeypatch):
    attempts = {}

    def fake_research(instrument, asset_type, job_dir, data_dir, allow_network):
        attempts[instrument] = attempts.get(instrument, 0) + 1
        job_dir.mkdir(parents=True, exist_ok=True)
        if instrument == "RETRY" and attempts[instrument] == 1:
            research._atomic_json(job_dir / "status.json", {
                "issues": [{"severity": "error", "message": "Temporary source failure"}],
            })
            return 1
        (job_dir / "report.md").write_text(f"# {instrument}\n", encoding="utf-8")
        return 0

    async def analyze(instrument, report, model):
        return "Reviewed.", model

    monkeypatch.setattr(research, "run_research", fake_research)
    items = [{"instrument": "DONE"}, {"instrument": "RETRY"}]
    first = asyncio.run(research.run_batch(
        items, tmp_path / "resume-batch", tmp_path / "data",
        allow_network=False, model="llama3.2:latest", analyze=analyze,
    ))
    assert first["status"] == "completed_with_errors"
    second = asyncio.run(research.run_batch(
        items, tmp_path / "resume-batch", tmp_path / "data",
        allow_network=False, model="llama3.2:latest", analyze=analyze, resume=True,
    ))
    assert second["status"] == "completed"
    assert attempts == {"DONE": 1, "RETRY": 2}


def test_batch_can_reanalyze_saved_reports_without_redownloading(tmp_path, monkeypatch):
    data_runs = []

    def fake_research(instrument, asset_type, job_dir, data_dir, allow_network):
        data_runs.append(instrument)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "report.md").write_text(f"# Saved report for {instrument}\n", encoding="utf-8")
        return 0

    versions = iter(("First review.", "Evidence-constrained review."))

    async def analyze(instrument, report, model):
        assert f"Saved report for {instrument}" in report
        return next(versions), model

    monkeypatch.setattr(research, "run_research", fake_research)
    items = [{"instrument": "NCK"}]
    directory = tmp_path / "analysis-refresh"
    first = asyncio.run(research.run_batch(
        items, directory, tmp_path / "data", allow_network=False,
        model="llama3.2:latest", analyze=analyze,
    ))
    second = asyncio.run(research.run_batch(
        items, directory, tmp_path / "data", allow_network=False,
        model="llama3.2:latest", analyze=analyze, resume=True,
        reanalyze_completed=True,
    ))
    assert first["status"] == second["status"] == "completed"
    assert data_runs == ["NCK"]
    assert "Evidence-constrained review." in (directory / "01-NCK" / "analysis.md").read_text(encoding="utf-8")
    assert "First review." in (directory / "01-NCK" / "analysis-previous.md").read_text(encoding="utf-8")


def test_comparison_chart_includes_all_batch_items_and_marks_missing_history(tmp_path):
    from datetime import timedelta

    batch = tmp_path / "comparison"
    first_date = research.date(2023, 1, 1)
    items = [
        {"instrument": "UP", "list_names": ["Watchlist"], "job_directory": "01-UP"},
        {"instrument": "DOWN", "list_names": ["Holdings"], "job_directory": "02-DOWN"},
        {"instrument": "NO_DATA", "list_names": [], "job_directory": "03-NO_DATA"},
    ]
    for item, final in zip(items[:2], (150.0, 80.0)):
        directory = batch / item["job_directory"]
        directory.mkdir(parents=True)
        bars = []
        for index in range(366):
            close = 100 + (final - 100) * index / 365
            bars.append({
                "date": (first_date + timedelta(days=index)).isoformat(),
                "close": close,
                "adjusted_close": close,
            })
        (directory / "market-data.json").write_text(json.dumps({
            "bars": bars,
        }), encoding="utf-8")
    (batch / "03-NO_DATA").mkdir()
    (batch / "status.json").write_text(json.dumps({"items": items}), encoding="utf-8")

    result = research.write_comparison_chart(batch)
    chart = (batch / "comparison.svg").read_text(encoding="utf-8")
    rows = list(research.csv.DictReader((batch / "comparison.csv").open(encoding="utf-8")))
    assert result == {
        "chart": "comparison.svg", "csv": "comparison.csv",
        "markdown": "comparison.md",
        "instruments": 3, "available": 2, "unavailable": 1, "period_days": 365,
    }
    assert "UP  (Watchlist)" in chart
    assert "DOWN  (Holdings)" in chart
    assert "NO_DATA" in chart and "Unavailable" in chart
    assert rows[0]["instrument"] == "UP"
    assert rows[0]["return_pct"] == "50.0"
    assert rows[-1]["status"] == "unavailable"


def test_comparison_chart_accepts_three_digit_batch_folder_indices(tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    items = [
        {"instrument": f"ASX:AB{index}", "list_names": [], "job_directory": f"{index:02d}-ASX-AB{index}"}
        for index in range(1, 100)
    ]
    items.append({
        "instrument": "ASX:AB100",
        "list_names": [],
        "job_directory": "100-ASX-AB100",
    })
    market_dir = batch / "100-ASX-AB100"
    market_dir.mkdir()
    (market_dir / "market-data.json").write_text(json.dumps({
        "bars": [
            {"date": "2025-01-01", "close": 10},
            {"date": "2026-01-01", "close": 12},
        ],
    }), encoding="utf-8")
    (batch / "status.json").write_text(json.dumps({"items": items}), encoding="utf-8")

    research.write_comparison_chart(batch)
    rows = list(research.csv.DictReader((batch / "comparison.csv").open(encoding="utf-8")))
    last = next(row for row in rows if row["instrument"] == "ASX:AB100")
    assert last["status"] == "available"
    assert last["return_pct"] == "20.0"
