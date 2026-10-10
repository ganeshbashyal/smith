from datetime import date

from assistant_api.finance.repository import FinanceStore


def test_additive_schema_creation_and_crud(tmp_path):
    store = FinanceStore(tmp_path / "finance.sqlite3")
    security_id = store.upsert_security("BHP", "BHP Group", "Materials", "Diversified miner")
    assert security_id
    assert store.get_security("BHP")["name"] == "BHP Group"
    assert len(store.list_securities()) == 1

    evidence_id, created = store.add_evidence(
        "BHP", "FY24 annual report", "BHP", "annual_report", "annual_report", "https://example.com/bhp.pdf",
        date(2024, 8, 1), "annual", "hash1", ["Revenue grew 5% to $50bn."],
    )
    assert created is True
    evidence_id2, created2 = store.add_evidence(
        "BHP", "FY24 annual report", "BHP", "annual_report", "annual_report", "https://example.com/bhp.pdf",
        date(2024, 8, 1), "annual", "hash1", ["Revenue grew 5% to $50bn."],
    )
    assert created2 is False
    assert evidence_id == evidence_id2

    results = store.search_evidence("BHP", "revenue growth")
    assert results and results[0]["id"].startswith("finance_evidence:")

    context = store.evidence_as_context("BHP")
    assert context and context[0]["source_type"] == "annual_report"

    statement_id = store.save_statement(
        "BHP", "annual", date(2024, 6, 30), "AUD", "millions", True,
        {"revenue": "50000", "net_income": "10000"}, evidence_id, "",
    )
    assert statement_id
    statements = store.list_statements("BHP")
    assert statements[0]["figures"]["revenue"] == "50000"

    revision = store.add_thesis_revision("BHP", thesis="Long China iron ore demand", evidence_ids=[evidence_id])
    assert revision == 1
    revision2 = store.add_thesis_revision("BHP", thesis="Revised: demand slowing", evidence_ids=[evidence_id])
    assert revision2 == 2
    revisions = store.list_thesis_revisions("BHP")
    assert [r["revision"] for r in revisions] == [1, 2]


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
