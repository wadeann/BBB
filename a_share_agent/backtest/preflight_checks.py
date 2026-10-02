from __future__ import annotations

from typing import Any

from .costs import board_aware_lot_size, price_limit_pct


def audit_benchmark_calendar(
    benchmark_dates: list[str],
    trusted_trading_dates: list[str],
    *,
    research_start: str,
    research_end: str,
    warmup_required: int,
) -> dict[str, Any]:
    all_dates = sorted({str(d) for d in benchmark_dates if str(d)})
    expected = sorted({str(d) for d in trusted_trading_dates if research_start <= str(d) <= research_end})
    period = [d for d in all_dates if research_start <= d <= research_end]
    warmup = [d for d in all_dates if d < research_start]
    missing = sorted(set(expected) - set(period))
    extra = sorted(set(period) - set(expected))
    exact_calendar_match = bool(expected and not missing and not extra and len(period) == len(expected))
    warmup_ok = len(warmup) >= int(warmup_required)
    return {
        "expected_trading_days": len(expected),
        "benchmark_trading_days": len(period),
        "benchmark_warmup_bars": len(warmup),
        "warmup_required": int(warmup_required),
        "missing_trading_dates": missing,
        "extra_trading_dates": extra,
        "exact_calendar_match": exact_calendar_match,
        "warmup_ok": warmup_ok,
        "complete": bool(exact_calendar_match and warmup_ok),
        "period_start": period[0] if period else None,
        "period_end": period[-1] if period else None,
    }


def audit_trading_rules_runtime() -> dict[str, Any]:
    checks = {
        "main_st_pre_20260706": price_limit_pct("600000.SH", "2026-07-03", is_st=True) == 0.05,
        "main_st_post_20260706": price_limit_pct("600000.SH", "2026-07-06", is_st=True) == 0.10,
        "main_ipo_day1_unlimited": price_limit_pct("600000.SH", "2026-01-02", trading_days_since_listing=1) == 999.0,
        "main_ipo_day6_normal": price_limit_pct("600000.SH", "2026-01-09", trading_days_since_listing=6) == 0.10,
        "star_ipo_day1_unlimited": price_limit_pct("688001.SH", "2026-01-02", trading_days_since_listing=1) == 999.0,
        "star_ipo_day6_20pct": price_limit_pct("688001.SH", "2026-01-09", trading_days_since_listing=6) == 0.20,
        "chinext_ipo_day1_unlimited": price_limit_pct("300001.SZ", "2026-01-02", trading_days_since_listing=1) == 999.0,
        "chinext_ipo_day6_20pct": price_limit_pct("300001.SZ", "2026-01-09", trading_days_since_listing=6) == 0.20,
        "bse_listing_day_unlimited": price_limit_pct("920002.BJ", "2026-01-02", trading_days_since_listing=1) == 999.0,
        "bse_day2_30pct": price_limit_pct("920002.BJ", "2026-01-05", trading_days_since_listing=2) == 0.30,
        "delisting_first_day_unlimited": price_limit_pct(
            "600000.SH",
            "2026-01-02",
            status="DELISTING",
            trading_days_in_delisting=1,
        ) == 999.0,
        "main_buy_lot": board_aware_lot_size("600000.SH", 150, direction="BUY") == 100,
        "star_buy_minimum": board_aware_lot_size("688001.SH", 199, direction="BUY") == 0,
        "star_buy_one_share_increment": board_aware_lot_size("688001.SH", 201, direction="BUY") == 201,
        "bse_buy_one_share_increment": board_aware_lot_size("920002.BJ", 101, direction="BUY") == 101,
        "main_odd_lot_full_sell": board_aware_lot_size("600000.SH", 50, direction="SELL", held_quantity=50) == 50,
        "star_odd_lot_full_sell": board_aware_lot_size("688001.SH", 150, direction="SELL", held_quantity=150) == 150,
        "bse_odd_lot_full_sell": board_aware_lot_size("920002.BJ", 80, direction="SELL", held_quantity=80) == 80,
    }
    return {
        "checks": checks,
        "passed": sum(1 for value in checks.values() if value),
        "total": len(checks),
        "verified": bool(checks and all(checks.values())),
        "failed_checks": sorted(name for name, value in checks.items() if not value),
    }
