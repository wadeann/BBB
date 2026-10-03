from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, median, pstdev
from typing import Any


def entry_context_quality(trade: dict) -> dict:
    """Evaluate whether a trade's entry context is usable for routing.

    Both regime AND theme must be known and have OK-quality data.
    """
    regime_state = (trade.get("regime_data_quality_at_signal") or {}).get("state")
    theme_state = (trade.get("theme_data_quality_at_signal") or {}).get("state")
    regime_known = trade.get("regime_at_signal") not in (None, "UNKNOWN", "unknown")
    theme_known = trade.get("theme_lifecycle") not in (None, "UNKNOWN", "unknown")
    regime_ok = regime_state == "ok"
    theme_ok = theme_state == "ok"
    usable = regime_ok and theme_ok and regime_known and theme_known
    return {
        "usable": usable,
        "regime_ok": regime_ok,
        "regime_known": regime_known,
        "theme_ok": theme_ok,
        "theme_known": theme_known,
    }


def _first_of(vals: list, *default):
    """Return the first truthy value, else the first default."""
    for v in vals:
        if v:
            return v
    return default[0] if default else None


def _quality_state(trade: dict) -> str:
    """Derive combined quality state for a trade from regime + theme."""
    ecq = entry_context_quality(trade)
    if ecq["usable"]:
        return "ok"
    if not ecq["regime_known"] or not ecq["theme_known"]:
        return "unknown"
    regime_s = (trade.get("regime_data_quality_at_signal") or {}).get("state", "") or "unknown"
    theme_s = (trade.get("theme_data_quality_at_signal") or {}).get("state", "") or "unknown"
    if "unavailable" in (regime_s, theme_s):
        return "unavailable"
    return "degraded"


def performance_metrics(equity_curve: list[dict[str,Any]], trades: list[dict[str,Any]], initial_cash: float) -> dict[str,Any]:
    if not equity_curve:
        return {}
    vals=[float(x["equity"]) for x in equity_curve]
    daily=[]
    for a,b in zip(vals,vals[1:]):
        daily.append(b/a-1 if a else 0)
    total=vals[-1]/initial_cash-1 if initial_cash else 0
    years=max((len(vals)-1)/252, 1/252)
    cagr=(vals[-1]/initial_cash)**(1/years)-1 if vals[-1]>0 and initial_cash>0 else -1
    peak=vals[0]; max_dd=0.0; dd_days=0; max_dd_days=0
    for v in vals:
        if v>=peak: peak=v; dd_days=0
        else:
            dd=v/peak-1; max_dd=min(max_dd,dd); dd_days+=1; max_dd_days=max(max_dd_days,dd_days)
    mu=mean(daily) if daily else 0; sd=pstdev(daily) if len(daily)>1 else 0
    sharpe=(mu/sd*math.sqrt(252)) if sd>0 else 0
    downside=[x for x in daily if x<0]; dsd=(sum(x*x for x in downside)/len(downside))**.5 if downside else 0
    sortino=(mu/dsd*math.sqrt(252)) if dsd>0 else 0
    calmar=(cagr/abs(max_dd)) if max_dd<0 else 0
    sells=[t for t in trades if t.get("direction")=="SELL" and t.get("pnl") is not None]
    wins=[t for t in sells if float(t["pnl"])>0]; losses=[t for t in sells if float(t["pnl"])<0]
    gross_profit=sum(float(t["pnl"]) for t in wins); gross_loss=abs(sum(float(t["pnl"]) for t in losses))
    pf=gross_profit/gross_loss if gross_loss>0 else (999.0 if gross_profit>0 else 0.0)
    avg_win=mean([float(t["pnl_pct"]) for t in wins]) if wins else 0
    avg_loss=mean([float(t["pnl_pct"]) for t in losses]) if losses else 0
    expectancy=mean([float(t["pnl_pct"]) for t in sells]) if sells else 0
    fees=sum(float(t.get("fees",0)) for t in trades)
    gross_pnl=sum(float(t.get("gross_pnl_before_costs",0) or 0) for t in sells)
    round_trip_fees=sum(float(t.get("round_trip_fees",0) or 0) for t in sells)
    slippage_cost=sum(float(t.get("round_trip_slippage",0) or 0) for t in sells)
    net_pnl=sum(float(t.get("pnl",0) or 0) for t in sells)
    streak=0; max_loss_streak=0
    for t in sells:
        if float(t.get("pnl",0))<0: streak+=1; max_loss_streak=max(max_loss_streak,streak)
        else: streak=0
    mfe_vals=[float(t.get("mfe_pct")) for t in sells if t.get("mfe_pct") is not None]
    mae_vals=[float(t.get("mae_pct")) for t in sells if t.get("mae_pct") is not None]
    return {
        "initial_cash": initial_cash,"ending_equity":vals[-1],"total_return":total,"cagr":cagr,
        "max_drawdown":max_dd,"max_drawdown_days":max_dd_days,"sharpe":sharpe,"sortino":sortino,"calmar":calmar,
        "closed_trades":len(sells),"win_rate":len(wins)/len(sells) if sells else 0,"profit_factor":pf,
        "avg_win_pct":avg_win,"avg_loss_pct":avg_loss,"expectancy_pct":expectancy,"total_fees":fees,
        "gross_pnl_before_costs":gross_pnl,"round_trip_fees":round_trip_fees,"estimated_slippage_cost":slippage_cost,
        "net_realized_pnl":net_pnl,"gross_return_on_initial":gross_pnl/initial_cash if initial_cash else 0.0,
        "net_realized_return_on_initial":net_pnl/initial_cash if initial_cash else 0.0,
        "max_consecutive_losses":max_loss_streak,"avg_mfe_pct":mean(mfe_vals) if mfe_vals else 0.0,"avg_mae_pct":mean(mae_vals) if mae_vals else 0.0,
    }


def monthly_returns(equity_curve: list[dict[str,Any]]) -> list[dict[str,Any]]:
    months: dict[str,list[dict[str,Any]]]=defaultdict(list)
    for x in equity_curve: months[str(x["date"])[:7]].append(x)
    out=[]; prev=None
    for m in sorted(months):
        end=float(months[m][-1]["equity"])
        if prev is None: start=float(months[m][0]["equity"])
        else: start=prev
        out.append({"month":m,"return":end/start-1 if start else 0,"ending_equity":end})
        prev=end
    return out


def grouped_trade_stats(trades: list[dict[str,Any]], key: str) -> list[dict[str,Any]]:
    groups=defaultdict(list)
    for t in trades:
        if t.get("direction")=="SELL" and t.get("pnl") is not None:
            groups[str(t.get(key) or "unknown")].append(t)
    out=[]
    for k,items in groups.items():
        pnl=sum(float(x["pnl"]) for x in items); wins=sum(1 for x in items if float(x["pnl"])>0)
        out.append({"group":k,"trades":len(items),"pnl":pnl,"win_rate":wins/len(items),"avg_pnl_pct":mean(float(x.get("pnl_pct",0)) for x in items)})
    return sorted(out,key=lambda x:x["pnl"],reverse=True)


def multi_key_trade_stats(trades: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    """Group closed trades by multiple keys and compute descriptive statistics.

    This function does not decide whether a pattern should be enabled. Router enablement
    belongs to the validation layer and must use out-of-sample evidence, not an in-sample
    win-rate shortcut.
    """
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        if t.get("direction") != "SELL" or t.get("pnl") is None:
            continue
        key_tuple = tuple(str(t.get(k) or "unknown") for k in keys)
        groups[key_tuple].append(t)

    out = []
    for kt, items in groups.items():
        wins = [t for t in items if float(t["pnl"]) > 0]
        losses = [t for t in items if float(t["pnl"]) < 0]
        returns = [float(t.get("pnl_pct", 0)) for t in items]
        win_rate = len(wins) / len(items) if items else 0.0
        avg_return = mean(returns) if returns else 0.0
        median_return = median(returns) if returns else 0.0
        gross_profit = sum(float(t["pnl"]) for t in wins)
        gross_loss = abs(sum(float(t["pnl"]) for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
        expectancy = mean(returns) if returns else 0.0
        avg_win = mean(float(t.get("pnl_pct", 0)) for t in wins) if wins else 0.0
        avg_loss = mean(float(t.get("pnl_pct", 0)) for t in losses) if losses else 0.0

        equity_index = 1.0
        peak = 1.0
        max_dd = 0.0
        ordered_items = sorted(items, key=lambda x: str(x.get("trade_date") or ""))
        for t in ordered_items:
            equity_index *= 1.0 + float(t.get("pnl_pct", 0))
            peak = max(peak, equity_index)
            dd = equity_index / peak - 1.0 if peak else 0.0
            max_dd = min(max_dd, dd)

        streak = 0
        max_loss_streak = 0
        for t in ordered_items:
            if float(t.get("pnl", 0)) < 0:
                streak += 1
                max_loss_streak = max(max_loss_streak, streak)
            else:
                streak = 0

        avg_holding = mean(float(t.get("holding_days", 0) or 0) for t in items) if items else 0.0
        mfe_vals = [float(t.get("mfe_pct", 0) or 0) for t in items if t.get("mfe_pct") is not None]
        mae_vals = [float(t.get("mae_pct", 0) or 0) for t in items if t.get("mae_pct") is not None]
        row = dict(zip(keys, kt))
        quality_states = [_quality_state(t) for t in items]
        q_ok = quality_states.count("ok")
        q_degraded = quality_states.count("degraded")
        q_unavailable = quality_states.count("unavailable")
        q_unknown = quality_states.count("unknown")
        total = len(items)
        quality_coverage = round(q_ok / total, 4) if total > 0 else 0.0
        row.update({
            "trades": len(items),
            "win_rate": round(win_rate, 4),
            "avg_return_pct": round(avg_return, 4),
            "median_return_pct": round(median_return, 4),
            "avg_win_pct": round(avg_win, 4),
            "avg_loss_pct": round(avg_loss, 4),
            "profit_factor": round(profit_factor, 2),
            "expectancy_pct": round(expectancy, 4),
            "max_drawdown_pct": round(max_dd, 4),
            "max_consecutive_losses": max_loss_streak,
            "avg_holding_days": round(avg_holding, 1),
            "avg_mfe_pct": round(mean(mfe_vals), 4) if mfe_vals else 0.0,
            "avg_mae_pct": round(mean(mae_vals), 4) if mae_vals else 0.0,
            "quality_ok_trades": q_ok,
            "quality_degraded_trades": q_degraded,
            "quality_unavailable_trades": q_unavailable,
            "quality_unknown_trades": q_unknown,
            "quality_coverage": quality_coverage,
            "context_quality": "ok" if quality_coverage == 1.0 else "mixed",
            "usable_for_router": quality_coverage == 1.0,
            "status": "INSUFFICIENT_DATA" if len(items) < 12 else "SUFFICIENT_DATA",
        })
        out.append(row)
    return sorted(out, key=lambda x: x.get("expectancy_pct", 0), reverse=True)


def regime_pattern_matrix(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate Regime × Pattern × Theme Lifecycle descriptive performance matrix."""
    return multi_key_trade_stats(trades, ["regime_at_signal", "pattern_id", "theme_lifecycle"])


_ENTRY_ATTRIBUTION_FIELDS = [
    "pattern_id",
    "pattern_version",
    "regime_at_signal",
    "theme",
    "theme_lifecycle",
    "theme_lifecycle_confidence_at_signal",
    "theme_data_quality_at_signal",
    "signal_strength",
    "regime_confidence_at_signal",
    "regime_data_quality_at_signal",
]


def _buy_map(trades: list[dict]) -> dict[str, dict]:
    """Build {round_trip_id: BUY_trade_dict} from trades list."""
    m: dict[str, dict] = {}
    seen: dict[str, int] = defaultdict(int)
    for t in trades:
        if t.get("direction") == "BUY" and t.get("round_trip_id"):
            rtid = str(t["round_trip_id"])
            seen[rtid] += 1
            if rtid not in m:
                m[rtid] = t
    _buy_map._duplicates = sorted(rid for rid, cnt in seen.items() if cnt > 1)  # type: ignore[attr-defined]
    return m


def audit_trade_attribution(trade: dict, *, buy_map: dict[str, dict] | None = None) -> dict:
    """Audit a single trade for attribution integrity."""
    round_trip_id = trade.get("round_trip_id")
    symbol = trade.get("symbol")
    errors: list[str] = []

    if trade.get("direction") == "BUY":
        if not round_trip_id:
            errors.append("MISSING_ROUND_TRIP_ID")
        signal_date = trade.get("signal_date")
        trade_date = trade.get("trade_date")
        if trade_date and signal_date and trade_date <= signal_date:
            errors.append("buy_trade_date must be after signal_date")

    if trade.get("direction") == "SELL":
        if not round_trip_id:
            errors.append("MISSING_ROUND_TRIP_ID")
        if not trade.get("pattern_id"):
            errors.append("missing pattern_id")
        if not trade.get("regime_at_signal"):
            errors.append("missing regime_at_signal")
        if trade.get("theme_lifecycle") is None:
            errors.append("missing theme_lifecycle")
        entry_date = trade.get("entry_date")
        trade_date = trade.get("trade_date")
        if trade_date and entry_date and trade_date < entry_date:
            errors.append("exit_date before entry_date")

        if buy_map is not None and round_trip_id:
            if round_trip_id in buy_map:
                buy_trade = buy_map[round_trip_id]
                for field in _ENTRY_ATTRIBUTION_FIELDS:
                    buy_val = buy_trade.get(field)
                    sell_val = trade.get(field)
                    if isinstance(buy_val, dict) and isinstance(sell_val, dict):
                        if sorted(buy_val.items()) != sorted(sell_val.items()):
                            errors.append(f"ENTRY_ATTRIBUTION_MISMATCH:{field}")
                    elif isinstance(buy_val, float) and isinstance(sell_val, float):
                        if abs(buy_val - sell_val) > 1e-9:
                            errors.append(f"ENTRY_ATTRIBUTION_MISMATCH:{field}")
                    elif buy_val != sell_val:
                        errors.append(f"ENTRY_ATTRIBUTION_MISMATCH:{field}")
            else:
                errors.append("missing_matching_buy_trade")

    return {"valid": len(errors) == 0, "errors": errors,
            "round_trip_id": round_trip_id, "symbol": symbol}


def batch_audit_attribution(trades: list[dict]) -> dict:
    """Audit closed round trips and fail closed on ambiguous BUY/SELL identities."""
    bm = _buy_map(trades)
    dup_buy_ids = list(getattr(_buy_map, "_duplicates", []))
    closed = [t for t in trades if t.get("direction") == "SELL"]

    sell_seen: dict[str, int] = defaultdict(int)
    for t in closed:
        rid = t.get("round_trip_id")
        if rid:
            sell_seen[str(rid)] += 1
    dup_sell_ids = sorted(rid for rid, cnt in sell_seen.items() if cnt > 1)

    results = []
    for t in closed:
        result = audit_trade_attribution(t, buy_map=bm)
        rid = str(t.get("round_trip_id")) if t.get("round_trip_id") else None
        if rid in dup_buy_ids:
            result["errors"].append("DUPLICATE_BUY_ROUND_TRIP_ID")
        if rid in dup_sell_ids:
            result["errors"].append("DUPLICATE_SELL_ROUND_TRIP_ID")
        result["valid"] = len(result["errors"]) == 0
        results.append(result)

    valid_results = [r for r in results if r["valid"]]
    invalid_results = [r for r in results if not r["valid"]]
    invalid_trades = [
        {"round_trip_id": r["round_trip_id"], "symbol": r["symbol"], "errors": r["errors"]}
        for r in invalid_results
    ]
    buy_ids = set(bm.keys())
    sell_ids = [str(t.get("round_trip_id")) for t in closed if t.get("round_trip_id")]
    missing_buys = sorted({rid for rid in sell_ids if rid not in buy_ids})
    missing_round_trip_ids = sum(1 for t in trades if t.get("direction") in {"BUY", "SELL"} and not t.get("round_trip_id"))
    duplicate_round_trip_ids = sorted(set(dup_buy_ids) | set(dup_sell_ids))

    return {
        "closed_trades": len(closed),
        "valid": len(valid_results),
        "invalid": len(invalid_results),
        "invalid_trades": invalid_trades,
        "duplicate_round_trip_ids": duplicate_round_trip_ids,
        "duplicate_buy_round_trip_ids": dup_buy_ids,
        "duplicate_sell_round_trip_ids": dup_sell_ids,
        "missing_round_trip_ids": missing_round_trip_ids,
        "missing_matching_buys": missing_buys,
        "errors": [e for r in invalid_results for e in r["errors"]],
    }
