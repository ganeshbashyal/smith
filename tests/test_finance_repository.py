from datetime import date
from pathlib import Path
import sqlite3

from assistant_api.finance.evidence import passages_with_page_numbers, plain_text_passages
from assistant_api.finance.repository import FinanceStore


def test_additive_schema_creation_and_crud(tmp_path):
    store = FinanceStore(tmp_path / "finance.sqlite3")
    security_id = store.upsert_security("BHP", "BHP Group", "Materials", "Diversified miner")
    assert security_id
    assert store.get_security("BHP")["name"] == "BHP Group"
    assert len(store.list_securities()) == 1

    passages = plain_text_passages("Revenue grew 5% to $50bn.")
    evidence_id, created = store.add_evidence(
        "BHP", "FY24 annual report", "BHP", "annual_report", "annual_report", "https://example.com/bhp.pdf",
        date(2024, 8, 1), "annual", "hash1", passages, page_count=1,
    )
    assert created is True
    evidence_id2, created2 = store.add_evidence(
        "BHP", "FY24 annual report", "BHP", "annual_report", "annual_report", "https://example.com/bhp.pdf",
        date(2024, 8, 1), "annual", "hash1", passages, page_count=1,
    )
    assert created2 is False
    assert evidence_id == evidence_id2

    results = store.search_evidence("BHP", "revenue growth")
    assert results and results[0]["id"].startswith("finance_evidence:")

    context = store.evidence_as_context("BHP")
    assert context and context[0]["source_type"] == "annual_report"

    statement_id, revision = store.save_statement(
        "BHP", "annual", date(2024, 6, 30), "AUD", "millions", True,
        {"revenue": "50000", "net_income": "10000"}, evidence_id, "",
    )
    assert statement_id and revision == 1
    statements = store.list_statements("BHP")
    assert statements[0]["figures"]["revenue"] == "50000"

    revision = store.add_thesis_revision("BHP", thesis="Long China iron ore demand", evidence_ids=[evidence_id])
    assert revision == 1
    revision2 = store.add_thesis_revision("BHP", thesis="Revised: demand slowing", evidence_ids=[evidence_id])
    assert revision2 == 2
    revisions = store.list_thesis_revisions("BHP")
    assert [r["revision"] for r in revisions] == [1, 2]


def test_statement_save_creates_new_revision_never_overwrites(tmp_path):
    """Defect 5 regression: confirming the same period twice must version, not upsert."""
    store = FinanceStore(tmp_path / "finance.sqlite3")
    store.upsert_security("BHP", "BHP Group", "Materials", "")
    end = date(2024, 6, 30)
    id1, rev1 = store.save_statement("BHP", "annual", end, "AUD", "millions", True,
                                     {"revenue": "50000"}, None, "First confirmed entry")
    id2, rev2 = store.save_statement("BHP", "annual", end, "AUD", "millions", True,
                                     {"revenue": "52000"}, None, "Corrected entry")
    assert rev1 == 1 and rev2 == 2
    assert id1 != id2

    revisions = store.list_statement_revisions("BHP", "annual", end.isoformat())
    assert [r["revision"] for r in revisions] == [1, 2]
    assert revisions[0]["figures"]["revenue"] == "50000"
    assert revisions[1]["figures"]["revenue"] == "52000"

    # list_statements/latest_statement only surface the latest revision per period.
    latest = store.list_statements("BHP")
    assert len(latest) == 1
    assert latest[0]["figures"]["revenue"] == "52000"
    assert latest[0]["revision"] == 2


def test_pdf_evidence_preserves_true_page_numbers_across_multi_chunk_pages(tmp_path):
    """Defect 6 regression: a page split into several chunks must still be one page."""
    store = FinanceStore(tmp_path / "finance.sqlite3")
    store.upsert_security("BHP", "BHP Group", "Materials", "")
    long_page = "A" * 5000  # longer than PASSAGE_CHARS -> splits into multiple chunks on page 1
    pages = [long_page, "Short page two text."]
    passages = passages_with_page_numbers(pages)
    assert len({p["page_number"] for p in passages}) == 2  # exactly 2 real pages
    assert max(p["chunk_index"] for p in passages if p["page_number"] == 1) > 1

    evidence_id, _ = store.add_evidence(
        "BHP", "FY24 annual report", "BHP", "annual_report", "annual_report", "",
        None, "annual", "hash-pdf", passages, page_count=len(pages),
    )
    doc = store.get_evidence(evidence_id)
    assert doc["page_count"] == 2  # not the chunk count


def test_watchlist_price_bars_alerts_and_catalysts(tmp_path):
    store = FinanceStore(tmp_path / "finance.sqlite3")
    watchlist_id = store.create_watchlist("Miners", ["BHP", "RIO"], "5")
    watchlist = store.get_watchlist(watchlist_id)
    assert watchlist["symbols"] == ["BHP", "RIO"]

    is_new = store.save_price_bar("BHP", "2024-01-01", "AUD", "45.0", "45.0", 1000, "2024-01-02T00:00:00Z")
    assert is_new is True
    is_new_again = store.save_price_bar("BHP", "2024-01-01", "AUD", "45.0", "45.0", 1000, "2024-01-02T00:00:00Z")
    assert is_new_again is False
    assert len(store.price_history("BHP")) == 1

    added = store.add_alert("BHP", "developing", "Price moved", "close 44 -> 45", "2024-01-01")
    assert added is True
    duplicate = store.add_alert("BHP", "developing", "Price moved", "close 44 -> 45", "2024-01-01")
    assert duplicate is False
    assert len(store.list_alerts("BHP")) == 1

    catalyst_id, created = store.add_catalyst("BHP", "Quarterly production report", date(2024, 4, 15),
                                              "Australia/Sydney", "https://example.com", True)
    assert created is True
    assert len(store.list_catalysts("BHP")) == 1


def test_valuation_runs_and_portfolio_holdings(tmp_path):
    store = FinanceStore(tmp_path / "finance.sqlite3")
    run_id = store.save_valuation_run("BHP", "ratios", {"price": "45"}, {"values": {"market_cap": "1000"}}, None)
    assert run_id
    runs = store.list_valuation_runs("BHP")
    assert runs[0]["result"]["values"]["market_cap"] == "1000"

    portfolio_id = store.create_portfolio("Main")
    assert store.get_portfolio(portfolio_id)["name"] == "Main"
    count = store.replace_holdings(portfolio_id, [
        {"symbol": "BHP", "units": "100", "cost_basis_total": "4000", "is_cash": False},
        {"symbol": "CASH", "units": "5000", "is_cash": True},
    ])
    assert count == 2
    holdings = store.list_holdings(portfolio_id)
    assert {h["symbol"] for h in holdings} == {"BHP", "CASH"}

    # Replace is atomic: a second call fully supersedes the first.
    store.replace_holdings(portfolio_id, [{"symbol": "RIO", "units": "10", "is_cash": False}])
    assert [h["symbol"] for h in store.list_holdings(portfolio_id)] == ["RIO"]


def test_single_row_holding_crud(tmp_path):
    store = FinanceStore(tmp_path / "finance.sqlite3")
    portfolio_id = store.create_portfolio("Main")
    holding_id = store.add_holding(portfolio_id, {"symbol": "BHP", "units": "10", "is_cash": False})
    assert holding_id
    assert len(store.list_holdings(portfolio_id)) == 1

    updated = store.update_holding(portfolio_id, holding_id, {"symbol": "BHP", "units": "20", "is_cash": False})
    assert updated is True
    assert store.list_holdings(portfolio_id)[0]["units"] == "20"

    deleted = store.delete_holding(portfolio_id, holding_id)
    assert deleted is True
    assert store.list_holdings(portfolio_id) == []
    assert store.delete_holding(portfolio_id, holding_id) is False


def test_generic_instrument_market_cache_journal_and_macro_storage(tmp_path):
    store = FinanceStore(tmp_path / "finance.sqlite3")
    instrument = store.upsert_instrument({
        "symbol": "XAUUSD=X", "name": "Gold / US dollar", "instrument_type": "xauusd_spot_proxy",
        "currency": "USD", "source_symbol": "XAUUSD=X",
    })
    assert instrument["instrument_type"] == "xauusd_spot_proxy"

    bar = {
        "instrument": "XAUUSD=X", "instrument_type": "xauusd_spot_proxy", "timeframe": "1h",
        "observed_at": "2026-10-27T12:00:00+00:00", "source": "Yahoo Finance test proxy",
        "currency": "USD", "open": "2400", "high": "2410", "low": "2390",
        "close": "2405", "volume": 10, "retrieved_at": "2026-10-27T12:01:00+00:00",
        "freshness": "fresh",
    }
    assert store.save_market_observations([bar]) == 1
    assert store.save_market_observations([bar]) == 0
    assert store.market_history("XAUUSD=X", "1h")[0]["close"] == "2405"

    entry_id = store.add_trading_journal_entry({
        "instrument": "XAUUSD=X", "direction": "long", "timeframe": "1h",
        "setup": "Breakout retest", "manual_chart_notes": "Manually observed range",
        "evidence": [{"source": "manual"}],
    })
    entry = store.list_trading_journal("XAUUSD=X")[0]
    assert entry["id"] == entry_id
    assert entry["evidence"] == [{"source": "manual"}]
    assert store.close_trading_journal_entry(
        entry_id, "50", "2026-10-27T13:00:00+00:00", "Wait for close",
    )
    assert not store.close_trading_journal_entry(
        entry_id, "50", "2026-10-27T13:00:00+00:00", "Duplicate close",
    )

    macro_id = store.add_gold_macro_evidence({
        "topic": "real_yields", "title": "Rates note", "publisher": "Manual",
        "observed_at": "2026-10-27T12:00:00+00:00", "summary": "Unverified observation",
        "status": "unverified", "evidence_ids": [1],
    })
    macro = store.list_gold_macro_evidence("real_yields")[0]
    assert macro["id"] == macro_id
    assert macro["evidence_ids"] == [1]


def test_verified_backup_created_before_first_finance_migration(tmp_path):
    from assistant_api.storage import Store
    data_dir = tmp_path
    Store(data_dir)  # creates the base mr_smith.sqlite3 with existing tables/data.
    db_path = data_dir / "mr_smith.sqlite3"
    assert db_path.exists() and db_path.stat().st_size > 0

    FinanceStore(db_path)
    backups = list(data_dir.glob("mr_smith.pre-finance-migration-*.sqlite3.bak"))
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0

    # A second FinanceStore attach against the same (already-migrated) file must not re-backup.
    FinanceStore(db_path)
    assert len(list(data_dir.glob("mr_smith.pre-finance-migration-*.sqlite3.bak"))) == 1


def test_finance_migration_backup_includes_committed_wal_data(tmp_path):
    from assistant_api.storage import Store

    Store(tmp_path)
    db_path = tmp_path / "mr_smith.sqlite3"
    live_connection = sqlite3.connect(db_path)
    try:
        assert live_connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        live_connection.execute("CREATE TABLE wal_probe (value TEXT NOT NULL)")
        live_connection.execute("INSERT INTO wal_probe VALUES ('preserve committed WAL data')")
        live_connection.commit()
        assert Path(str(db_path) + "-wal").exists()

        FinanceStore(db_path)

        backups = list(tmp_path.glob("mr_smith.pre-finance-migration-*.sqlite3.bak"))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert backup.execute("SELECT value FROM wal_probe").fetchone()[0] == (
                "preserve committed WAL data"
            )
    finally:
        live_connection.close()
