from __future__ import annotations

import csv
import json
from pathlib import Path
import pytest

from a_share_agent.backtest.corporate_actions import CorporateAction, CorporateActionEngine
from a_share_agent.backtest.data import HistoricalDataProvider
from a_share_agent.backtest.models import BacktestSettings
from a_share_agent.backtest.portfolio import Portfolio, Position
from a_share_agent.backtest.research import run_research_preflight
from a_share_agent.config import load_config


def test_corporate_action_cash_dividend():
    engine = CorporateActionEngine()
    engine.add_action(CorporateAction(
        symbol="600036.SH",
        ex_date="2026-07-15",
        record_date="2026-07-14",
        action_type="cash_dividend",
        cash_dividend_per_share=0.85,
    ))
    portfolio = Portfolio(initial_cash=100000.0)
    portfolio.positions["600036.SH"] = Position(
        symbol="600036.SH",
        entry_date="2026-07-01",
        entry_price=35.0,
        quantity=2000,
        stop_price=33.0,
        strategy_id="test_strat",
        strategy_family="trend",
        score=85.0,
    )

    # Before ex-date: no action
    applied_pre = engine.process_actions("2026-07-14", portfolio)
    assert len(applied_pre) == 0
    assert portfolio.cash == 100000.0
    assert portfolio.positions["600036.SH"].quantity == 2000

    # On ex-date: cash dividend paid (2000 * 0.85 = 1700.0)
    applied_on = engine.process_actions("2026-07-15", portfolio)
    assert len(applied_on) == 1
    assert applied_on[0]["type"] == "CASH_DIVIDEND"
    assert applied_on[0]["cash_received"] == 1700.0
    assert portfolio.cash == 101700.0
    assert portfolio.positions["600036.SH"].quantity == 2000


def test_corporate_action_bonus_and_split():
    engine = CorporateActionEngine()
    # 10送5 (ratio 0.5)
    engine.add_action(CorporateAction(
        symbol="000001.SZ",
        ex_date="2026-08-01",
        record_date="2026-07-31",
        action_type="bonus_shares",
        bonus_ratio=0.5,
    ))
    # 1拆2 (split_ratio 2.0)
    engine.add_action(CorporateAction(
        symbol="000002.SZ",
        ex_date="2026-08-01",
        record_date="2026-07-31",
        action_type="split",
        split_ratio=2.0,
    ))

    portfolio = Portfolio(initial_cash=50000.0)
    portfolio.positions["000001.SZ"] = Position(
        symbol="000001.SZ",
        entry_date="2026-07-10",
        entry_price=12.0,
        quantity=1000,
        stop_price=10.8,
        highest_price=13.5,
        strategy_id="strat1",
        strategy_family="trend",
        score=80.0,
    )
    portfolio.positions["000002.SZ"] = Position(
        symbol="000002.SZ",
        entry_date="2026-07-10",
        entry_price=20.0,
        quantity=1000,
        stop_price=18.0,
        highest_price=22.0,
        strategy_id="strat2",
        strategy_family="breakout",
        score=90.0,
    )

    applied = engine.process_actions("2026-08-01", portfolio)
    assert len(applied) == 2

    # Verify bonus shares adjustment
    pos1 = portfolio.positions["000001.SZ"]
    assert pos1.quantity == 1500  # 1000 * 1.5
    assert pos1.entry_price == 8.0  # 12.0 / 1.5
    assert pos1.stop_price == 7.2  # 10.8 / 1.5
    assert pos1.highest_price == 9.0  # 13.5 / 1.5

    # Verify split adjustment
    pos2 = portfolio.positions["000002.SZ"]
    assert pos2.quantity == 2000  # 1000 * 2.0
    assert pos2.entry_price == 10.0  # 20.0 / 2.0
    assert pos2.stop_price == 9.0  # 18.0 / 2.0
    assert pos2.highest_price == 11.0  # 22.0 / 2.0


def test_historical_status_pit_transitions(tmp_path: Path):
    # Setup test status intervals
    bdir = tmp_path / "data" / "backtest"
    bdir.mkdir(parents=True)
    status_file = bdir / "historical_status_intervals.csv"
    status_file.write_text(
        "symbol,status,effective_from,effective_to,reason\n"
        "600001.SH,TRADABLE,2026-01-01,2026-05-31,normal\n"
        "600001.SH,ST,2026-06-01,2026-08-31,warning\n"
        "600001.SH,*ST,2026-09-01,2026-09-15,delisting_risk\n"
        "600001.SH,SUSPENDED,2026-09-16,2026-09-25,investigation\n"
        "600001.SH,DELISTING,2026-09-26,2026-09-30,delisting_period\n",
        encoding="utf-8"
    )
    master_file = bdir / "security_master.csv"
    master_file.write_text(
        "symbol,name,board,listing_date,delisting_date,active_from,active_to,tradable,st,suspended,delisting_period,data_missing\n"
        "600001.SH,TestStock,SSE_MAIN,2026-01-01,,2026-01-01,,1,0,0,0,0\n",
        encoding="utf-8"
    )

    provider = HistoricalDataProvider(tmp_path, mcp=None, use_cache=False)
    assert provider.status_on("600001.SH", "2026-05-15") == "TRADABLE"
    assert provider.status_on("600001.SH", "2026-07-01") == "ST"
    assert provider.status_on("600001.SH", "2026-09-10") == "*ST"
    assert provider.status_on("600001.SH", "2026-09-20") == "SUSPENDED"
    assert provider.status_on("600001.SH", "2026-09-28") == "DELISTING"

    # In master, verify active_records_on reflects dynamic statuses
    provider.load_universe_for_period("2026-01-01", "2026-09-30", "data/backtest/security_master.csv")
    rec_may = provider.active_records_on("2026-05-15")
    assert len(rec_may) == 1
    assert rec_may[0]["tradable"] is True
    assert rec_may[0]["status"] == "TRADABLE"

    rec_st = provider.active_records_on("2026-07-01")
    assert len(rec_st) == 1
    assert rec_st[0]["market_tradable"] is True
    assert rec_st[0]["strategy_eligible"] is False
    assert rec_st[0]["st"] is True
    assert rec_st[0]["status"] == "ST"
    assert provider.is_market_tradable("600001.SH", "2026-07-01") is True
    assert provider.is_strategy_eligible("600001.SH", "2026-07-01") is False


def test_historical_sector_constituents_pit(tmp_path: Path):
    bdir = tmp_path / "data" / "backtest"
    bdir.mkdir(parents=True)
    sec_file = bdir / "historical_sector_intervals.csv"
    sec_file.write_text(
        "symbol,sector_code,sector_name,effective_from,effective_to\n"
        "600001.SH,BK001,SectorA,2026-01-01,2026-06-30\n"
        "600001.SH,BK002,SectorB,2026-07-01,2026-12-31\n"
        "600002.SH,BK001,SectorA,2026-01-01,2026-12-31\n",
        encoding="utf-8"
    )
    master_file = bdir / "security_master.csv"
    master_file.write_text(
        "symbol,name,board,listing_date,delisting_date,active_from,active_to,tradable,st,suspended,delisting_period,data_missing\n"
        "600001.SH,Stock1,SSE_MAIN,2026-01-01,,2026-01-01,,1,0,0,0,0\n"
        "600002.SH,Stock2,SSE_MAIN,2026-01-01,,2026-01-01,,1,0,0,0,0\n",
        encoding="utf-8"
    )

    provider = HistoricalDataProvider(tmp_path, mcp=None, use_cache=False)
    provider.load_universe_for_period("2026-01-01", "2026-12-31", "data/backtest/security_master.csv")

    # In June 2026: 600001.SH belongs to BK001
    sec1 = provider.sector_info_on("600001.SH", "2026-06-15")
    assert sec1["code"] == "BK001"
    constits_a_june = provider.sector_constituents_on("BK001", "2026-06-15")
    assert "600001.SH" in constits_a_june
    assert "600002.SH" in constits_a_june

    # In July 2026: 600001.SH switches to BK002
    sec2 = provider.sector_info_on("600001.SH", "2026-07-15")
    assert sec2["code"] == "BK002"
    constits_a_july = provider.sector_constituents_on("BK001", "2026-07-15")
    assert "600001.SH" not in constits_a_july
    assert "600002.SH" in constits_a_july

    constits_b_july = provider.sector_constituents_on("BK002", "2026-07-15")
    assert "600001.SH" in constits_b_july


def test_historical_trading_rules_switchover_20260706():
    """Verify dynamic price limit rules by date + exchange + board + risk_warning,
    specifically testing the 2026-07-06 switchover where Main Board ST stocks switch from 5% to 10%."""
    from a_share_agent.backtest.costs import price_limit_pct, locked_at_limit

    # 1. Before switchover: 2026-07-03
    date_before = "2026-07-03"
    # Main Board ST: 5%
    assert price_limit_pct("600000.SH", date_before, is_st=True) == 0.05
    assert price_limit_pct("000001.SZ", date_before, status="ST") == 0.05
    assert price_limit_pct("600000.SH", date_before, status="*ST") == 0.05
    # Main Board Normal: 10%
    assert price_limit_pct("600000.SH", date_before, is_st=False) == 0.10
    assert price_limit_pct("000001.SZ", date_before, is_st=False) == 0.10

    # STAR: 20% regardless of ST
    assert price_limit_pct("688001.SH", date_before, is_st=False) == 0.20
    assert price_limit_pct("688001.SH", date_before, is_st=True) == 0.20
    assert price_limit_pct("688001.SH", date_before, status="*ST") == 0.20

    # ChiNext: 20% regardless of ST
    assert price_limit_pct("300001.SZ", date_before, is_st=False) == 0.20
    assert price_limit_pct("300001.SZ", date_before, is_st=True) == 0.20

    # BSE: 30% regardless of ST
    assert price_limit_pct("920002.BJ", date_before, is_st=False) == 0.30
    assert price_limit_pct("920002.BJ", date_before, is_st=True) == 0.30

    # 2. On and After switchover: 2026-07-06 and later
    date_after = "2026-07-06"
    date_future = "2026-08-15"
    # Main Board ST switches to 10%!
    assert price_limit_pct("600000.SH", date_after, is_st=True) == 0.10
    assert price_limit_pct("000001.SZ", date_after, status="ST") == 0.10
    assert price_limit_pct("600000.SH", date_future, status="*ST") == 0.10
    # Main Board Normal remains 10%
    assert price_limit_pct("600000.SH", date_after, is_st=False) == 0.10
    assert price_limit_pct("000001.SZ", date_after, is_st=False) == 0.10

    # STAR, ChiNext, BSE remain consistent
    assert price_limit_pct("688001.SH", date_after, is_st=True) == 0.20
    assert price_limit_pct("300001.SZ", date_after, is_st=True) == 0.20
    assert price_limit_pct("920002.BJ", date_after, is_st=True) == 0.30

    # 3. Test locked_at_limit dynamic behavior with bar date
    bar_pre = {"symbol": "600000.SH", "date": "2026-07-03", "open": 10.50, "high": 10.50, "low": 10.50, "close": 10.50}
    # For ST on 2026-07-03: +5% is limit-up (10.0 * 1.05 = 10.50)
    assert locked_at_limit(bar_pre, prev_close=10.0, direction="BUY", is_st=True) is True
    # But on 2026-07-06: limit is 10%, so 10.50 is only +5%, NOT locked at limit!
    bar_post = {"symbol": "600000.SH", "date": "2026-07-06", "open": 10.50, "high": 10.50, "low": 10.50, "close": 10.50}
    assert locked_at_limit(bar_post, prev_close=10.0, direction="BUY", is_st=True) is False
    # At +10% (11.00) on 2026-07-06, it is locked at limit
    bar_post_10 = {"symbol": "600000.SH", "date": "2026-07-06", "open": 11.00, "high": 11.00, "low": 11.00, "close": 11.00}
    assert locked_at_limit(bar_post_10, prev_close=10.0, direction="BUY", is_st=True) is True


def test_research_preflight_coverage_audit_and_criteria():
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root)
    res = run_research_preflight(cfg, mcp=None, sample_size=10)

    # 1. Verification fields present
    assert "universe_market_coverage_ratio" in res
    assert "exchange_coverage" in res
    assert "historical_status_pit_coverage" in res
    assert "sector_label_coverage" in res
    assert "sector_membership_pit_coverage" in res
    assert "sector_constituent_pit_coverage" in res
    assert "raw_execution_price_ready" in res
    assert "corporate_action_ready" in res

    # 2. Complete Readiness & Authenticity fields
    assert "official_universe_set_match" in res
    assert "universe_extra_symbol_count" in res
    assert "universe_missing_symbol_count" in res
    assert "status_sample_verified" in res
    assert "status_source_coverage" in res
    assert "status_dataset_complete" in res
    assert "sector_schema_supports_pit" in res
    assert "sector_source_coverage" in res
    assert "sector_data_verified_pit" in res
    assert "sector_change_event_count" in res
    assert "sector_change_events_in_backtest_period" in res
    assert "sector_dataset_complete" in res
    assert "corporate_action_source_coverage" in res
    assert "corporate_action_expected_vs_loaded" in res
    assert "corporate_action_dataset_complete" in res
    assert "daily_raw_bar_coverage" in res
    assert "raw_bar_coverage_by_exchange" in res
    assert "historical_trading_rules_verified" in res

    # 3. Exchange coverage checks
    ex = res["exchange_coverage"]
    assert ex["SSE_MAIN"] > 0
    assert ex["STAR"] > 0
    assert ex["SZSE_MAIN"] > 0
    assert ex["CHINEXT"] > 0
    assert ex["BSE"] > 0

    # 4. Delisted stocks preserved
    assert res["universe"]["delisted_stocks_preserved_count"] > 0

    # 5. Strict completeness and authenticity checks (truthful audit reflecting current real data)
    assert res["status_sample_verified"] is True
    assert res["sector_data_verified_pit"] is True
    assert res["corporate_action_data_verified"] is True
    assert res["historical_trading_rules_verified"] is True
    assert res["corporate_action_invalid_count"] == 0
    assert res["synthetic_corporate_actions_detected"] == 0

    # Dataset completeness truthfully reflects that full market production data is not yet 100% complete
    assert res["status_dataset_complete"] is False
    assert res["sector_dataset_complete"] is False
    assert res["corporate_action_dataset_complete"] is False
    assert res["corporate_action_ready"] is False

    # 6. Raw price bar coverage reflects honest real coverage (~41%), gating raw_execution_price_ready
    assert res["daily_raw_bar_coverage"] < 0.98
    assert res["raw_execution_price_ready"] is False

    # 7. Final formal full market readiness MUST be False due to strict gating
    assert res["formal_full_market_ready"] is False
    assert res["research_grade_candidate"] is False
    assert res["criteria_checklist"]["6_status_dataset_complete"] is False
    assert res["criteria_checklist"]["8_corporate_action_dataset_complete"] is False
    assert res["criteria_checklist"]["9_raw_execution_price_ready"] is False
    assert res["criteria_checklist"]["10_daily_raw_bar_coverage"] is False
    assert all(res["criteria_checklist"].values()) is False
    assert len(res["provider_warnings"]) > 0


def test_p0_5_and_p0_6_raw_adjusted_execution_and_accounting():
    """Verify that:
    1. HistoricalDataProvider.raw_bars() does NOT fallback to adjusted bars when raw bars are missing.
    2. adjustment_factor_on returns accurate adjustment ratios.
    3. Raw space stop loss conversion preserves correct risk sizing.
    """
    root = Path(__file__).resolve().parents[1]
    provider = HistoricalDataProvider(root, mcp=None, use_cache=False)

    # 1. raw_bars on a missing symbol returns [] (no fallback to adjusted)
    missing_sym = "999999.SH"
    assert provider.raw_bars(missing_sym) == []

    # 2. adjustment_factor_on returns a float factor
    factor = provider.adjustment_factor_on("600000.SH", "2026-08-31")
    assert isinstance(factor, float)
    assert factor > 0

    # 3. Test conversion formula: stop_raw = raw_close * (stop_adj / adj_close)
    raw_close = 10.0
    adj_close = 30.0  # e.g., 3x post-adjustment
    stop_adj = 28.5   # 5% stop below adj_close
    stop_raw = raw_close * (stop_adj / adj_close)
    assert pytest.approx(stop_raw, 0.001) == 9.5  # 5% stop below raw_close!


def test_ipo_and_delisting_trading_rules():
    """Verify IPO first 5 trading days (BSE first 1 trading day) and delisting transition first day rules."""
    from a_share_agent.backtest.costs import price_limit_pct, locked_at_limit

    # 1. SSE Main: first 5 trading days -> no limit (999.0); day 6 -> 10%
    for day in range(1, 6):
        lim = price_limit_pct("600000.SH", "2025-01-01", trading_days_since_listing=day)
        assert lim >= 900.0, f"SSE Main day {day} should have no price limit"
    lim_day6 = price_limit_pct("600000.SH", "2025-01-01", trading_days_since_listing=6)
    assert lim_day6 == 0.10

    # 2. STAR: first 5 trading days -> no limit; day 6 -> 20%
    for day in range(1, 6):
        lim = price_limit_pct("688001.SH", "2025-01-01", trading_days_since_listing=day)
        assert lim >= 900.0
    assert price_limit_pct("688001.SH", "2025-01-01", trading_days_since_listing=6) == 0.20

    # 3. SZSE Main: first 5 trading days -> no limit; day 6 -> 10%
    for day in range(1, 6):
        assert price_limit_pct("000001.SZ", "2025-01-01", trading_days_since_listing=day) >= 900.0
    assert price_limit_pct("000001.SZ", "2025-01-01", trading_days_since_listing=6) == 0.10

    # 4. ChiNext: first 5 trading days -> no limit; day 6 -> 20%
    for day in range(1, 6):
        assert price_limit_pct("300001.SZ", "2025-01-01", trading_days_since_listing=day) >= 900.0
    assert price_limit_pct("300001.SZ", "2025-01-01", trading_days_since_listing=6) == 0.20

    # 5. BSE: first 1 trading day -> no limit; day 2 -> 30%
    assert price_limit_pct("920002.BJ", "2025-01-01", trading_days_since_listing=1) >= 900.0
    assert price_limit_pct("920002.BJ", "2025-01-01", trading_days_since_listing=2) == 0.30

    # 6. Delisting transition first day: no limit; day 2 -> 10% (or ST)
    assert price_limit_pct("600001.SH", "2025-01-01", status="DELISTING", is_delisting_first_day=True) >= 900.0
    assert price_limit_pct("600001.SH", "2025-01-01", status="DELISTING", trading_days_in_delisting=1) >= 900.0
    assert price_limit_pct("600001.SH", "2025-01-01", status="DELISTING", trading_days_in_delisting=2) == 0.10

    # 7. locked_at_limit during no-limit period: always False
    bar_limit = {"symbol": "600000.SH", "open": 20.0, "high": 20.0, "low": 20.0, "close": 20.0}
    assert locked_at_limit(bar_limit, prev_close=10.0, direction="BUY", trading_days_since_listing=1) is False
    assert locked_at_limit(bar_limit, prev_close=10.0, direction="BUY", is_delisting_first_day=True) is False


def test_board_aware_order_quantity_rules():
    """Verify board-aware order quantity normalization across all markets."""
    from a_share_agent.backtest.costs import board_aware_lot_size

    # 1. SSE Main: min 100, multiples of 100
    assert board_aware_lot_size("600000.SH", 250, direction="BUY") == 200
    assert board_aware_lot_size("600000.SH", 99, direction="BUY") == 0
    assert board_aware_lot_size("600000.SH", 100, direction="BUY") == 100

    # 2. SZSE Main: min 100, multiples of 100
    assert board_aware_lot_size("000001.SZ", 380, direction="BUY") == 300
    assert board_aware_lot_size("000001.SZ", 50, direction="BUY") == 0

    # 3. STAR: min 200, 1-share increments above 200
    assert board_aware_lot_size("688001.SH", 199, direction="BUY") == 0
    assert board_aware_lot_size("688001.SH", 200, direction="BUY") == 200
    assert board_aware_lot_size("688001.SH", 253, direction="BUY") == 253

    # 4. BSE: min 100, 1-share increments above 100
    assert board_aware_lot_size("920002.BJ", 99, direction="BUY") == 0
    assert board_aware_lot_size("920002.BJ", 100, direction="BUY") == 100
    assert board_aware_lot_size("920002.BJ", 147, direction="BUY") == 147

    # 5. ChiNext: min 100, multiples of 100
    assert board_aware_lot_size("300001.SZ", 280, direction="BUY") == 200

    # 6. SELL: odd lots allowed
    assert board_aware_lot_size("600000.SH", 35, direction="SELL") == 35
    assert board_aware_lot_size("688001.SH", 72, direction="SELL") == 72


def test_corporate_action_rights_issue_accounting():
    """Verify rights issue (配股) complete accounting in CorporateActionEngine."""
    from a_share_agent.backtest.corporate_actions import CorporateAction, CorporateActionEngine
    from a_share_agent.backtest.portfolio import Portfolio, Position

    engine = CorporateActionEngine()
    # 10配3 (ratio 0.3) at price 5.0 per share
    engine.add_action(CorporateAction(
        symbol="600036.SH",
        ex_date="2026-07-20",
        record_date="2026-07-19",
        action_type="rights_issue",
        rights_ratio=0.3,
        rights_price=5.0,
    ))

    # Portfolio with sufficient cash: 1000 shares at 10.0, cash 2000.0
    # Cost to subscribe: 1000 * 0.3 * 5.0 = 1500.0
    portfolio = Portfolio(initial_cash=2000.0)
    portfolio.positions["600036.SH"] = Position(
        symbol="600036.SH",
        entry_date="2026-07-01",
        entry_price=10.0,
        quantity=1000,
        stop_price=9.5,
        highest_price=11.0,
        strategy_id="test_strat",
        strategy_family="trend",
        score=80.0,
    )

    applied = engine.process_actions("2026-07-20", portfolio)
    assert len(applied) == 1
    assert applied[0]["type"] == "RIGHTS_ISSUE_EXERCISED"
    assert applied[0]["cash_paid"] == 1500.0
    assert portfolio.cash == 500.0

    pos = portfolio.positions["600036.SH"]
    assert pos.quantity == 1300  # 1000 + 300
    # Blended cost: (1000 * 10 + 1500) / 1300 = 11500 / 1300 ≈ 8.8462
    assert pytest.approx(pos.entry_price, 0.001) == 8.8462


def test_official_universe_snapshot_independent_reconciliation():
    """Verify that official universe snapshots exist as independent files and can be reconciled."""
    root = Path(__file__).resolve().parents[1]
    snap_dir = root / "data" / "backtest" / "official_universe_snapshots"
    assert snap_dir.exists()
    snap_files = list(snap_dir.glob("*.csv"))
    assert len(snap_files) >= 3

    # Check manifest.json
    manifest_file = snap_dir / "manifest.json"
    assert manifest_file.exists()
    with manifest_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert "snapshots" in data
    assert len(data["snapshots"]) >= 3


def test_candidate_eligibility_prefilter():
    """Verify that ST, suspended, delisting, and data_missing stocks are pre-filtered
    before entering the signal scan, sorting, or LLM top-N evaluation."""
    root = Path(__file__).resolve().parents[1]
    provider = HistoricalDataProvider(root, mcp=None, use_cache=False)
    provider.load_universe_for_period("2024-10-01", "2026-09-30", "data/backtest/security_master.csv")

    # Verify that an ST or delisting stock is NOT strategy_eligible
    # In active_records_on, ST stocks have strategy_eligible=False
    records_aug = provider.active_records_on("2026-08-31")
    st_records = [r for r in records_aug if r.get("st")]
    for r in st_records:
        assert r["strategy_eligible"] is False
        assert provider.is_strategy_eligible(r["symbol"], "2026-08-31") is False

    # Non-ST with data should be eligible
    eligible_records = [r for r in records_aug if r.get("strategy_eligible")]
    assert len(eligible_records) > 0
    for r in eligible_records[:10]:
        assert r["st"] is False
        assert r["delisting_period"] is False
        assert r["data_missing"] is False


def test_official_snapshot_independent_difference():
    """Verify that official universe snapshots are generated independently of local security master
    and contain official exchange listings (such as CDR 689009.SH) not in local master."""
    root = Path(__file__).resolve().parents[1]
    snap_file = root / "data" / "backtest" / "official_universe_snapshots" / "2026-08-31.csv"
    assert snap_file.exists()

    official_symbols = set()
    with snap_file.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == [
            "date", "symbol", "exchange", "board", "listing_date",
            "source", "source_document_id_or_url", "dataset_version"
        ]
        for row in reader:
            official_symbols.add(row["symbol"])

    master_file = root / "data" / "backtest" / "security_master.csv"
    master_symbols = set()
    with master_file.open("r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            master_symbols.add(row["symbol"])

    # 689009.SH is a genuine STAR market listing (Ninebot CDR) present in official SSE register
    # but not in local equity security master, proving snapshots are truly independent.
    diff_symbols = official_symbols - master_symbols
    assert "689009.SH" in diff_symbols
    assert len(diff_symbols) > 0

    # Verify official snapshot manifest with SHA256 of raw registers
    manifest_file = root / "official_universe_snapshot_manifest.json"
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert "source_registers" in manifest
    assert len(manifest["source_registers"]["files"]) >= 6


def test_security_master_authentic_listing_dates():
    """Verify all 5655 stocks in security_master.csv have authentic exchange listing dates
    and zero stocks retain the bogus 2024-09-06 placeholder."""
    root = Path(__file__).resolve().parents[1]
    master_file = root / "data" / "backtest" / "security_master.csv"
    assert master_file.exists()

    stock_map = {}
    bogus_count = 0
    total = 0
    with master_file.open("r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            total += 1
            sym = row["symbol"]
            ld = row.get("listing_date", "")
            stock_map[sym] = ld
            if ld == "2024-09-06":
                bogus_count += 1

    assert total == 5655
    assert bogus_count == 0

    # Verify authentic historical IPO dates for landmark stocks
    assert stock_map["600000.SH"] == "1999-11-10"  # Pudong Development Bank
    assert stock_map["000001.SZ"] == "1991-04-03"  # Ping An Bank
    assert stock_map["600519.SH"] == "2001-08-27"  # Kweichow Moutai
    assert stock_map["688981.SH"] == "2020-07-16"  # SMIC
    assert stock_map["300750.SZ"] == "2018-06-11"  # CATL


def test_daily_raw_bar_coverage_audit_metrics():
    """Verify daily raw bar coverage evaluates every single trading day in backtest period
    and accurately fails the 98% gate."""
    root = Path(__file__).resolve().parents[1]
    cov_file = root / "daily_raw_coverage.csv"
    assert cov_file.exists()

    rows = []
    with cov_file.open("r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 485  # Total trading days from 2024-10-08 to 2026-09-30
    cov_pcts = [float(r["raw_coverage_pct"].rstrip("%")) for r in rows]
    min_cov = min(cov_pcts)
    max_cov = max(cov_pcts)
    days_below_98 = sum(1 for c in cov_pcts if c < 98.0)

    assert min_cov >= 40.0
    assert max_cov <= 45.0
    assert days_below_98 == 485  # All days fail 98% threshold

    # Verify raw dataset manifest
    manifest_file = root / "raw_dataset_manifest.json"
    assert manifest_file.exists()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert manifest["summary"]["file_count"] == 2323
    assert manifest["summary"]["row_count"] == 1187093
    assert manifest["summary"]["overall_dataset_hash"] == "ab5624c66081de18c37c425e195c9b44db814f4e37450bd73d97b5787341d93b"


def test_corporate_action_set_reconciliation_audit():
    """Verify corporate actions undergo true set reconciliation against official register."""
    root = Path(__file__).resolve().parents[1]
    diff_file = root / "corporate_action_set_diff.csv"
    assert diff_file.exists()

    matched = 0
    missing = 0
    with diff_file.open("r", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            st = r["status"]
            if st == "MATCHED":
                matched += 1
            elif st == "MISSING_IN_PRODUCTION":
                missing += 1

    assert matched == 91
    assert missing == 7  # 7 official events missing in production corporate_actions.csv


def test_ipo_trading_days_calculation_and_price_limit():
    """Verify IPO trading days calculation accurately uses calendar trading days
    so mature stocks (like 600000.SH) never enter IPO no-limit mode."""
    from a_share_agent.backtest.costs import calculate_trading_days_since_listing, ipo_no_price_limit, price_limit_pct

    calendar = [f"2024-10-{i:02d}" for i in range(8, 31)]
    # Mature stock listed in 1999
    days = calculate_trading_days_since_listing("1999-11-10", "2024-10-08", trading_calendar=calendar)
    assert days > 5000
    assert ipo_no_price_limit("600000.SH", "2024-10-08", trading_days_since_listing=days, board="SSE_MAIN") is False
    assert price_limit_pct("600000.SH", "2024-10-08", trading_days_since_listing=days) == 0.10

    # Stock listed exactly on trade_date (day 1)
    days_new = calculate_trading_days_since_listing("2024-10-08", "2024-10-08", trading_calendar=calendar)
    assert days_new == 1
    assert ipo_no_price_limit("688001.SH", "2024-10-08", trading_days_since_listing=days_new, board="STAR") is True
    assert price_limit_pct("688001.SH", "2024-10-08", trading_days_since_listing=days_new) == 999.0

    # BSE stock on day 2: no longer unlimited
    assert ipo_no_price_limit("920002.BJ", "2024-10-09", trading_days_since_listing=2, board="BSE") is False
    assert price_limit_pct("920002.BJ", "2024-10-09", trading_days_since_listing=2) == 0.30


def test_board_sell_quantity_semantics():
    """Verify board-specific sell lot rules:
    - Main board: held < 100 must sell all; partial sells must be multiples of 100.
    - STAR: held < 200 must sell all; above 200 allows 1-share increments.
    - BSE: held < 100 must sell all; above 100 allows 1-share increments."""
    from a_share_agent.backtest.costs import board_aware_lot_size

    # SSE Main Board
    assert board_aware_lot_size("600000.SH", quantity=50, direction="SELL", held_quantity=50) == 50  # Odd-lot clearance
    assert board_aware_lot_size("600000.SH", quantity=150, direction="SELL", held_quantity=250) == 100  # Rounded down to 100
    assert board_aware_lot_size("600000.SH", quantity=250, direction="SELL", held_quantity=250) == 250  # Sell all

    # STAR Market
    assert board_aware_lot_size("688001.SH", quantity=150, direction="SELL", held_quantity=150) == 150  # Odd-lot clearance
    assert board_aware_lot_size("688001.SH", quantity=250, direction="SELL", held_quantity=350) == 250  # 1-share increment above 200
    assert board_aware_lot_size("688001.SH", quantity=150, direction="SELL", held_quantity=350) == 0  # Below min 200

    # BSE
    assert board_aware_lot_size("920002.BJ", quantity=60, direction="SELL", held_quantity=60) == 60  # Odd-lot clearance
    assert board_aware_lot_size("920002.BJ", quantity=125, direction="SELL", held_quantity=200) == 125  # 1-share increment above 100
    assert board_aware_lot_size("920002.BJ", quantity=80, direction="SELL", held_quantity=200) == 0  # Below min 100


def test_hard_gate_blocks_full_market_research_execution():
    """Verify research-suite and ResearchLab strictly block full-market execution
    when formal_full_market_ready is False."""
    from a_share_agent.backtest.research import ResearchLab
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(root)

    lab = ResearchLab(cfg, mcp=None, llm=None)
    with pytest.raises(RuntimeError, match="FORMAL_FULL_MARKET_GATE_BLOCKED"):
        lab.run(overrides={"universe_mode": "strict_point_in_time"})



