from __future__ import annotations

import csv
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

    # 5. Strict completeness and authenticity checks
    assert res["official_universe_set_match"] is True
    assert res["universe_extra_symbol_count"] == 0
    assert res["universe_missing_symbol_count"] == 0
    assert res["status_dataset_complete"] is True
    assert res["status_source_coverage"] >= 0.99
    assert res["sector_dataset_complete"] is True
    assert res["sector_source_coverage"] >= 0.99
    assert res["sector_change_events_in_backtest_period"] >= 8
    assert res["corporate_action_dataset_complete"] is True
    assert res["corporate_action_source_coverage"] >= 0.99
    assert res["corporate_action_ready"] is True
    assert res["historical_trading_rules_verified"] is True

    # 6. Raw price bar coverage threshold >= 98%
    assert res["daily_raw_bar_coverage"] >= 0.98
    for b_cov in res["raw_bar_coverage_by_exchange"].values():
        assert b_cov >= 0.98
    assert res["raw_execution_price_ready"] is True

    # 7. Final formal full market readiness
    assert res["formal_full_market_ready"] is True
    assert res["research_grade_candidate"] is True
    assert all(res["criteria_checklist"].values())
