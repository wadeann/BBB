from a_share_agent.backtest.provenance_audit import (
    is_a_share_common_equity_symbol,
    local_record_active_on,
)


def test_universe_membership_reconciliation_is_independent_of_trading_status():
    """ST/suspension affects tradability, not whether the security belongs to the market universe."""
    row = {
        "symbol": "600000.SH",
        "board": "SSE_MAIN",
        "listing_date": "1999-11-10",
        "active_from": "1999-11-10",
        "active_to": "",
        "delisting_date": "",
        "st": "1",
        "suspended": "1",
    }
    assert is_a_share_common_equity_symbol(row["symbol"], row["board"])
    assert local_record_active_on(row, "2026-07-01") is True


def test_universe_membership_respects_delisting_boundary():
    row = {
        "symbol": "600001.SH",
        "board": "SSE_MAIN",
        "listing_date": "2000-01-01",
        "active_from": "2000-01-01",
        "active_to": "2025-06-30",
        "delisting_date": "2025-06-30",
    }
    assert local_record_active_on(row, "2025-06-30") is True
    assert local_record_active_on(row, "2025-07-01") is False


def test_a_share_asset_filter_excludes_non_target_codes():
    assert is_a_share_common_equity_symbol("600000.SH", "SSE_MAIN")
    assert is_a_share_common_equity_symbol("688981.SH", "STAR")
    assert not is_a_share_common_equity_symbol("900957.SH", "SSE_MAIN")
    assert not is_a_share_common_equity_symbol("200413.SZ", "SZSE_MAIN")
    assert not is_a_share_common_equity_symbol("689009.SH", "STAR")
