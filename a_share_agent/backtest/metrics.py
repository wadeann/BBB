from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean, pstdev
from typing import Any


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
