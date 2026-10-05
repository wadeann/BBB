from __future__ import annotations

import hashlib
import uuid

import bisect
import json
import math
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import RuntimeConfig
from ..strategy.signal_engine import DeterministicSignalEngine
from ..strategy.router import StrategyRouter
from .costs import AShareCostModel, locked_at_limit, price_limit_pct, board_aware_lot_size, calculate_trading_days_since_listing
from .data import HistoricalDataProvider
from .metrics import performance_metrics, monthly_returns, grouped_trade_stats, regime_pattern_matrix, batch_audit_attribution
from .models import BacktestSettings, PendingOrder
from .portfolio import Portfolio
from .regime import market_context_from_benchmarks, sector_context_from_history
from .scoring import deterministic_score
from ..strategy.resonance import sector_resonance_filter
from .llm_filter import HistoricalLLMFilter


def _atr(bars: list[dict[str,Any]], n: int=14) -> float:
    if len(bars) < n+1: return 0.0
    trs=[]
    for i in range(1,len(bars)):
        h=float(bars[i]["high"]); l=float(bars[i]["low"]); pc=float(bars[i-1]["close"])
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    return sum(trs[-n:])/n if len(trs)>=n else 0.0


def _bar_map(bars: list[dict[str,Any]]) -> dict[str,dict[str,Any]]:
    return {str(x["date"]):x for x in bars}


class BacktestEngine:
    """Daily-bar, next-session execution backtest.

    Daily-close signals are generated with data available through that close only.
    Entries/exits created from close signals execute no earlier than the next session.
    Stop-loss checks can execute intraday using the day's high/low, subject to T+1.
    """
    def __init__(self, runtime_config: RuntimeConfig, provider: HistoricalDataProvider, settings: BacktestSettings, llm_filter: HistoricalLLMFilter | None = None):
        self.cfg=runtime_config
        self.provider=provider
        self.s=settings
        self.llm_filter=llm_filter
        self.signal_engine=DeterministicSignalEngine()
        self.router=StrategyRouter(runtime_config.strategy_router)
        self.costs=AShareCostModel(settings.commission_rate,settings.commission_min,settings.stamp_tax_rate_sell,settings.transfer_fee_rate,settings.slippage_bps)
        self.logs: list[dict[str,Any]]=[]
        self.rejections: list[dict[str,Any]]=[]
        self.data_quality: dict[str,Any]={"missing_bars":[],"sector_history_missing":[],"warnings":[]}
        self.enabled_strategies=set(settings.enabled_strategies or [])
        self.disabled_strategies=set(settings.disabled_strategies or [])

    def _log(self, date: str, kind: str, **payload: Any) -> None:
        self.logs.append({"date":date,"type":kind,**payload})

    def _next_date(self, dates: list[str], i: int) -> str | None:
        return dates[i+1] if i+1 < len(dates) else None

    def _stop_for_entry(self, hist: list[dict[str,Any]], entry_ref: float) -> float:
        atr=_atr(hist,14)
        recent_low=min(float(x["low"]) for x in hist[-10:]) if hist else entry_ref*.95
        atr_stop=entry_ref-2*atr if atr>0 else entry_ref*.95
        # Use the less distant valid protective stop, but not tighter than 1.5%.
        candidate=max(recent_low*.995,atr_stop)
        return min(entry_ref*.985, candidate)

    @staticmethod
    def _primary_hit(hits: list[dict[str,Any]]) -> dict[str,Any] | None:
        prim=[h for h in hits if h.get("strength")=="primary"]
        return prim[0] if prim else None

    @staticmethod
    def _exit_hit(hits: list[dict[str,Any]]) -> dict[str,Any] | None:
        ex=[h for h in hits if h.get("strength")=="exit"]
        priority={"ma20_break":1,"shooting_star_high":2,"ma_bearish_cut":3,"volume_price_divergence":4}
        return sorted(ex,key=lambda x:priority.get(str(x.get("signal")),99))[0] if ex else None

    def _eligible(self, symbol: str, d: str) -> bool:
        fn=getattr(self.provider,"eligible_on",None)
        return bool(fn(symbol,d)) if callable(fn) else True

    def _route(self, market: dict[str,Any], sector: dict[str,Any]) -> dict[str,Any]:
        if self.s.route_mode == "disabled":
            return {
                "route_id":"ROUTER_DISABLED",
                "allowed_strategy_families":["trend_breakout","trend_pullback","rebound_reversal","pattern_confirmation","exit_defensive"],
                "conditional_strategy_families":[],"blocked_strategy_families":[],
                "position_multiplier":1.0,"candidate_threshold_delta":0.0,"max_new_positions_override":None,
                "market_regime":market.get("market_regime"),"sector_strength":sector.get("sector_strength"),
                "route_reasons":["research_ablation:router_disabled"],"data_quality":{"state":"ok"},
            }
        return self.router.route(market_context=market,sector_context=sector)

    def _sector_context(self, bars: list[dict[str,Any]], d: str, name: str | None) -> dict[str,Any]:
        if self.s.sector_mode == "disabled":
            return {"as_of":d,"sector":name,"sector_strength":"neutral","sector_lifecycle":"unknown","sector_score":50.0,"data_quality":{"state":"ok","research_ablation":"sector_disabled"}}
        return sector_context_from_history(bars,d,name=name,fallback_neutral=self.s.sector_mode!="strict")

    def run(self, symbols: list[str], evaluation_window: dict[str, Any] | None = None) -> dict[str,Any]:
        # Validate evaluation_window if provided
        if evaluation_window is not None:
            if not isinstance(evaluation_window, dict):
                raise TypeError("evaluation_window must be a dict or None")
            for _key in ("fold_id", "entry_start", "entry_end_exclusive", "observation_end_exclusive"):
                if _key not in evaluation_window:
                    raise ValueError(f"evaluation_window missing required key: {_key}")
            self.s.start_date = evaluation_window["entry_start"]
            self.s.end_date = (datetime.strptime(evaluation_window["observation_end_exclusive"], "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
        benchmark_symbols=list(dict.fromkeys([self.s.benchmark, *[str(x) for x in self.cfg.defaults.get("benchmarks",{}).values()]]))
        benchmark_series={sym:self.provider.bars(sym,count=max(900,self.s.warmup_bars+550)) for sym in benchmark_symbols}
        benchmark=benchmark_series.get(self.s.benchmark) or next((v for v in benchmark_series.values() if v),[])
        if not benchmark:
            raise RuntimeError(f"benchmark bars unavailable: {self.s.benchmark}")
        dates=[b["date"] for b in benchmark if self.s.start_date <= b["date"] <= self.s.end_date]
        if not dates:
            raise RuntimeError("no benchmark trading dates in requested range")
        if evaluation_window is not None:
            dates = [d for d in dates if d < evaluation_window["observation_end_exclusive"]]

        # v0.7: symbols is a fallback/seed universe only. In point-in-time mode the
        # provider returns a different active universe for every historical date.
        seed_symbols=list(dict.fromkeys(symbols))
        bars_by_symbol: dict[str,list[dict[str,Any]]]={}
        raw_bars_by_symbol: dict[str,list[dict[str,Any]]]={}
        bar_dates_by_symbol: dict[str,list[str]]={}
        raw_dates_by_symbol: dict[str,list[str]]={}
        map_by_symbol: dict[str,dict[str,dict[str,Any]]]={}
        raw_map_by_symbol: dict[str,dict[str,dict[str,Any]]]={}
        invalid_symbols: set[str]=set()
        sector_bars: dict[str,list[dict[str,Any]]]={}
        sector_context_cache: dict[tuple[str,str],dict[str,Any]]={}
        sector_sources: dict[str,int]={}
        sector_heat_daily: list[dict[str,Any]]=[]
        universe_daily: list[dict[str,Any]]=[]
        seen_symbols: set[str]=set()
        sector_symbols_seen: set[tuple[str,str]] = set()
        sector_symbols_checked: set[str] = set()
        sector_symbols_mapped: set[str] = set()
        sector_symbols_historical: set[str] = set()

        def ensure_symbol(sym: str) -> bool:
            if sym in bars_by_symbol:
                return True
            if sym in invalid_symbols:
                return False
            rows=self.provider.bars(sym,count=max(900,self.s.warmup_bars+550))
            if len(rows)<60 or rows[-1]["date"] < self.s.start_date:
                invalid_symbols.add(sym)
                if sym not in self.data_quality["missing_bars"]:
                    self.data_quality["missing_bars"].append(sym)
                return False
            bars_by_symbol[sym]=rows
            raw_fn = getattr(self.provider, "raw_bars", None)
            raw_rows = raw_fn(sym, count=max(900, self.s.warmup_bars+550)) if callable(raw_fn) else []
            raw_bars_by_symbol[sym]=raw_rows
            raw_map_by_symbol[sym]=_bar_map(raw_rows)
            raw_dates_by_symbol[sym]=[str(x.get("date") or x.get("time")) for x in raw_rows]
            bar_dates_by_symbol[sym]=[str(x["date"]) for x in rows]
            map_by_symbol[sym]=_bar_map(rows)
            return True

        def hist_to(sym: str, d: str, limit: int | None = None) -> list[dict[str,Any]]:
            if not ensure_symbol(sym):
                return []
            idx=bisect.bisect_right(bar_dates_by_symbol[sym],d)
            start=max(0,idx-limit) if limit else 0
            return bars_by_symbol[sym][start:idx]

        def sector_for(sym: str, d: str) -> tuple[dict[str,Any],dict[str,Any]]:
            strict=self.s.sector_mode=="strict"
            sector_on=getattr(self.provider,"sector_info_on",None)
            info=sector_on(sym,d,strict=strict) if callable(sector_on) else self.provider.sector_info(sym)
            source=str(info.get("source") or "unknown")
            sector_symbols_checked.add(sym)
            if info.get("code") or info.get("name"):
                sector_symbols_mapped.add(sym)
                if source.startswith("historical"):
                    sector_symbols_historical.add(sym)
            pair=(sym,source)
            if pair not in sector_symbols_seen:
                sector_sources[source]=sector_sources.get(source,0)+1
                sector_symbols_seen.add(pair)
            code=info.get("code")
            if code and str(code) not in sector_bars:
                sector_bars[str(code)]=self.provider.sector_bars(str(code))
            sbars=sector_bars.get(str(code),[]) if code else []
            if code and not sbars:
                sc=str(code)
                if sc not in self.data_quality["sector_history_missing"]:
                    self.data_quality["sector_history_missing"].append(sc)
            cache_key=(str(code or "NO_SECTOR"),d)
            if cache_key not in sector_context_cache:
                sector_context_cache[cache_key]=self._sector_context(sbars,d,info.get("name"))
            return info,sector_context_cache[cache_key]

        portfolio=Portfolio(self.s.initial_cash)
        pending: list[PendingOrder]=[]
        equity_curve=[]
        candidates_count=0
        signal_count=0
        current_prices: dict[str,float]={}
        route_stats: dict[str,int]={}
        signal_stats: dict[str,int]={}
        executed_entries_today: dict[str,int]={}

        for di,d in enumerate(dates):
            next_d=self._next_date(dates,di)
            market=market_context_from_benchmarks(benchmark_series,d)

            # Historical point-in-time universe is reconstructed for each date.
            active_fn=getattr(self.provider,"active_records_on",None)
            active_records=active_fn(d,seed_symbols) if callable(active_fn) else [{"symbol":s,"tradable":True} for s in seed_symbols if self._eligible(s,d)]
            active_symbols=[str(x["symbol"]) for x in active_records if x.get("symbol")]
            seen_symbols.update(active_symbols)
            meta_fn=getattr(self.provider,"daily_universe_meta",None)
            umeta=meta_fn(d,seed_symbols) if callable(meta_fn) else {"date":d,"active_symbols":len(active_symbols),"source":"explicit_or_synthetic","point_in_time":False,"universe_hash":None}
            universe_daily.append(umeta)
            pit_enabled=bool(getattr(self.provider,"point_in_time_universe_enabled",False))
            if self.s.universe_mode=="strict_point_in_time" and pit_enabled and not bool(umeta.get("point_in_time")):
                raise RuntimeError(f"strict point-in-time universe lost at {d}")

            # 0) Process point-in-time Corporate Actions for day d
            ca_engine = getattr(self.provider, "corporate_actions", None)
            if ca_engine is not None and hasattr(ca_engine, "process_actions"):
                ca_events = ca_engine.process_actions(d, portfolio)
                for ev in ca_events:
                    self._log(d, "CORPORATE_ACTION", **{k:v for k,v in ev.items() if k != "date"})
                    if ev.get("type") == "RIGHTS_ISSUE_INSUFFICIENT_CASH" or "STRICT_RESEARCH_INVALID" in str(ev.get("warning", "")):
                        self.data_quality["strict_invalid"] = True
                        self.data_quality["strict_invalid_reason"] = "RIGHTS_ISSUE_INSUFFICIENT_CASH: cash insufficient to exercise mandatory rights issue"
                        if self.s.universe_mode == "strict_point_in_time":
                            raise RuntimeError(f"STRICT_RESEARCH_INVALID: Rights issue encountered with insufficient cash for {ev.get('symbol')} on {d}; run terminated.")

            # Update prices for active symbols plus current positions. Last known close
            # remains in current_prices for suspended holdings.
            mark_symbols=set(active_symbols)|set(portfolio.positions)
            for sym in mark_symbols:
                if not ensure_symbol(sym):
                    continue
                raw_bar=raw_map_by_symbol.get(sym,{}).get(d)
                if raw_bar:
                    current_prices[sym]=float(raw_bar["close"])

            # Build historical sector heat once per day. Strength is based on data
            # available through d and, when enabled, relative rank among active sectors.
            daily_sector_by_symbol: dict[str,tuple[dict[str,Any],dict[str,Any]]]={}
            unique_sector_contexts: dict[str,dict[str,Any]]={}
            if self.s.sector_mode != "disabled":
                for sym in active_symbols:
                    info,ctx=sector_for(sym,d)
                    daily_sector_by_symbol[sym]=(info,ctx)
                    code=str(info.get("code") or "")
                    if code:
                        unique_sector_contexts[code]=ctx
                if self.s.sector_relative_ranking and unique_sector_contexts:
                    ranked=sorted(unique_sector_contexts.items(),key=lambda kv:float(kv[1].get("sector_score",50.0)))
                    n=len(ranked)
                    for rank,(code,ctx) in enumerate(ranked):
                        pct=(rank+1)/n
                        score=float(ctx.get("sector_score",50.0))
                        ctx["relative_percentile"]=round(pct,4)
                        original=str(ctx.get("sector_strength","neutral"))
                        if original != "unknown":
                            if pct>=0.80 and score>=50:
                                ctx["sector_strength"]="strong"
                            elif pct<=0.20 or score<45:
                                ctx["sector_strength"]="weak"
                            else:
                                ctx["sector_strength"]="neutral"
                    top=sorted(ranked,key=lambda kv:float(kv[1].get("sector_score",50.0)),reverse=True)[:10]
                    sector_heat_daily.append({"date":d,"top":[{"code":code,"name":ctx.get("sector"),"score":ctx.get("sector_score",50),"relative_percentile":ctx.get("relative_percentile"),"strength":ctx.get("sector_strength"),"lifecycle":ctx.get("sector_lifecycle")} for code,ctx in top]})

            # 1) Execute pending orders scheduled for today at today's open.
            todays=[o for o in pending if o.execute_date==d]
            pending=[o for o in pending if o.execute_date!=d]
            for o in todays:
                if not ensure_symbol(o.symbol):
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"NO_EXECUTION_BAR","order":o.to_dict()}); continue
                raw_bar=raw_map_by_symbol.get(o.symbol,{}).get(d)
                if not raw_bar:
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"DATA_MISSING_RAW","order":o.to_dict()}); continue
                
                # Retrieve previous raw bar to check price limits in pure raw space
                r_dates = raw_dates_by_symbol.get(o.symbol, [])
                r_idx = bisect.bisect_left(r_dates, d)
                if r_idx <= 0:
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"DATA_MISSING_RAW_PREV","order":o.to_dict()}); continue
                prev_raw_bar = raw_bars_by_symbol[o.symbol][r_idx - 1]
                prev_raw_close = float(prev_raw_bar["close"])

                b = {**raw_bar, "symbol": o.symbol}
                sym_status = getattr(self.provider, "status_on", lambda s, dt: "")(o.symbol, d)
                listing_date = getattr(self.provider, "listing_date_on", lambda s: "")(o.symbol)
                days_since_listing = calculate_trading_days_since_listing(listing_date, d, trading_calendar=dates)
                is_delist_1st = (sym_status == "DELISTING" and getattr(self.provider, "delisting_days_on", lambda s, dt: 1)(o.symbol, d) == 1)
                if self.s.block_open_at_limit and locked_at_limit(
                    b, prev_raw_close, o.direction, status=sym_status, as_of=d,
                    trading_days_since_listing=days_since_listing,
                    is_delisting_first_day=is_delist_1st
                ):
                    self.rejections.append({"date": d, "symbol": o.symbol, "reason": "LOCKED_AT_PRICE_LIMIT", "order": o.to_dict()})
                    continue
                if o.direction == "SELL":
                    if hasattr(self.provider, "is_market_tradable") and not self.provider.is_market_tradable(o.symbol, d):
                        self.rejections.append({"date": d, "symbol": o.symbol, "reason": "SUSPENDED_CANNOT_SELL", "order": o.to_dict()})
                        continue
                    pos = portfolio.positions.get(o.symbol)
                    if pos:
                        sym_board = getattr(self.provider, "board_on", lambda s, dt: "")(o.symbol, d)
                        sell_qty = board_aware_lot_size(o.symbol, o.requested_quantity if o.requested_quantity > 0 else pos.quantity, direction="SELL", board=sym_board, held_quantity=pos.quantity)
                        if sell_qty <= 0:
                            self.rejections.append({"date": d, "symbol": o.symbol, "reason": "SELL_QUANTITY_INVALID_ODD_LOT", "order": o.to_dict()})
                            continue
                    exit_sector = daily_sector_by_symbol.get(o.symbol, ({"name":None},{"sector_lifecycle":"unknown","lifecycle":"unknown"}))
                    exit_sector_ctx = exit_sector[1] if isinstance(exit_sector, tuple) else {"sector_lifecycle":"unknown","lifecycle":"unknown"}
                    tr = portfolio.sell(symbol=o.symbol, date=d, signal_date=o.created_date, raw_price=float(raw_bar["open"]), reason=o.reason, cost_model=self.costs,
                        exit_regime=market.get("regime") or market.get("market_regime"),
                        exit_theme=exit_sector_ctx.get("sector") or o.sector,
                        exit_theme_lifecycle=exit_sector_ctx.get("lifecycle") or exit_sector_ctx.get("sector_lifecycle"))
                    if tr:
                        self._log(d, "TRADE", trade=tr.to_dict())
                    continue
                if o.symbol in portfolio.positions:
                    continue
                if o.symbol not in set(active_symbols) or not self._eligible(o.symbol, d):
                    self.rejections.append({"date": d, "symbol": o.symbol, "reason": "NOT_POINT_IN_TIME_ELIGIBLE", "order": o.to_dict()})
                    continue
                if len(portfolio.positions)>=self.s.max_positions:
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"MAX_POSITIONS","order":o.to_dict()}); continue
                equity=portfolio.equity(current_prices); mv=portfolio.market_value(current_prices)
                raw=float(raw_bar["open"])
                stop=float(o.stop_price or raw*.95)
                if stop >= raw:
                    stop = raw * 0.95
                raw_qty=portfolio.size_for_risk(equity=equity,price=self.costs.slip_price(raw,"BUY"),stop=stop,
                    risk_per_trade=self.s.risk_per_trade,max_single=self.s.max_single_position,max_total=self.s.max_total_position,
                    current_market_value=mv,multiplier=o.route_multiplier,lot=1)
                if o.requested_quantity>0: raw_qty=min(raw_qty,o.requested_quantity)
                sym_board = getattr(self.provider, "board_on", lambda s, dt: "")(o.symbol, d)
                qty = board_aware_lot_size(o.symbol, raw_qty, direction="BUY", board=sym_board)
                if qty<=0:
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"RISK_SIZE_ZERO","order":o.to_dict()}); continue
                tr=portfolio.buy(symbol=o.symbol,date=d,signal_date=o.created_date,raw_price=raw,quantity=qty,stop_price=stop,
                    strategy_id=str(o.strategy_id),strategy_family=str(o.strategy_family),score=float(o.score or 0),route_id=o.route_id,sector=o.sector,
                    cost_model=self.costs,meta=o.signal_meta,
                    pattern_id=o.pattern_id,pattern_version=o.pattern_version,
                    regime_at_signal=o.regime_at_signal,
                    regime_confidence_at_signal=o.regime_confidence_at_signal,
                    regime_data_quality_at_signal=o.regime_data_quality_at_signal,
                    theme=o.theme,theme_lifecycle=o.theme_lifecycle,
                    theme_lifecycle_confidence=o.theme_lifecycle_confidence,
                    theme_data_quality=o.theme_data_quality,
                    signal_strength=o.signal_strength,
                    round_trip_id=o.round_trip_id)
                if tr:
                    executed_entries_today[d]=executed_entries_today.get(d,0)+1; self._log(d,"TRADE",trade=tr.to_dict())

            # 2) Intraday protective stop / position lifecycle. T+1: entry date cannot sell.
            for sym,pos in list(portfolio.positions.items()):
                if not ensure_symbol(sym):
                    continue
                raw_b=raw_map_by_symbol.get(sym,{}).get(d)
                if not raw_b: continue
                pos.highest_price=max(pos.highest_price,float(raw_b["high"])); pos.lowest_price=min(pos.lowest_price or pos.entry_price,float(raw_b["low"]));
                if d>pos.entry_date: pos.holding_days += 1
                if d>pos.entry_date and float(raw_b["low"]) <= pos.stop_price:
                    if evaluation_window is not None:
                        if (hasattr(self.provider, "is_market_tradable") and not self.provider.is_market_tradable(sym, d)):
                            continue
                    raw_exec=min(float(raw_b["open"]),pos.stop_price) if float(raw_b["open"])<pos.stop_price else pos.stop_price
                    exit_sec = daily_sector_by_symbol.get(sym, ({"name":pos.sector},{"sector_lifecycle":"unknown","lifecycle":"unknown"}))
                    exit_sec_ctx = exit_sec[1] if isinstance(exit_sec, tuple) else {"sector_lifecycle":"unknown","lifecycle":"unknown"}
                    tr=portfolio.sell(symbol=sym,date=d,signal_date=d,raw_price=raw_exec,reason="STOP_LOSS",cost_model=self.costs,
                        exit_regime=market.get("regime") or market.get("market_regime"),
                        exit_theme=exit_sec_ctx.get("sector") or pos.sector,
                        exit_theme_lifecycle=exit_sec_ctx.get("lifecycle") or exit_sec_ctx.get("sector_lifecycle"))
                    if tr: self._log(d,"TRADE",trade=tr.to_dict())

            # 3) Close-confirmed exits -> next open.
            for sym,pos in list(portfolio.positions.items()):
                hist=hist_to(sym,d,120)
                if len(hist)<60 or hist[-1]["date"]!=d: continue
                _secinfo,sector=daily_sector_by_symbol.get(sym) or sector_for(sym,d)
                hits=self.signal_engine.scan(hist,market_regime=str(market.get("market_regime")),sector_strength=str(sector.get("sector_strength")))
                ex=self._exit_hit(hits)
                reason=None
                if ex: reason=f"SIGNAL:{ex['signal']}"
                elif pos.holding_days>=self.s.max_holding_days: reason="MAX_HOLDING_DAYS"
                if reason and next_d and not any(o.symbol==sym and o.direction=="SELL" for o in pending):
                    pending.append(PendingOrder(sym,"SELL",d,next_d,reason,pos.strategy_id,pos.strategy_family,pos.score,pos.route_id,pos.sector))
                    self._log(d,"EXIT_SIGNAL",symbol=sym,reason=reason,execute_date=next_d)

            # 4) Full-market entry scan over this date's PIT universe.
            _in_entry_window = evaluation_window is None or d < evaluation_window["entry_end_exclusive"]
            daily_candidates=[]
            for sym in active_symbols:
                if sym in portfolio.positions or any(o.symbol==sym and o.direction=="BUY" for o in pending): continue
                # Candidate Eligibility Pre-filter:
                # ST / *ST / suspended / delisting / DATA_MISSING_RAW must not enter candidate sorting or LLM top-N
                if hasattr(self.provider, "is_strategy_eligible") and not self.provider.is_strategy_eligible(sym, d):
                    continue
                if sym not in raw_map_by_symbol or d not in raw_map_by_symbol[sym]:
                    continue
                hist=hist_to(sym,d,120)
                if len(hist)<60 or hist[-1]["date"]!=d: continue
                secinfo,sector=daily_sector_by_symbol.get(sym) or sector_for(sym,d)
                route=self._route(market,sector)
                route_stats[route["route_id"]]=route_stats.get(route["route_id"],0)+1
                hits=self.signal_engine.scan(hist,market_regime=str(market.get("market_regime")),sector_strength=str(sector.get("sector_strength")))
                filtered_hits=[]
                for hit in hits:
                    sid0=str(hit.get("signal"))
                    if hit.get("strength")=="primary":
                        if self.enabled_strategies and sid0 not in self.enabled_strategies: continue
                        if sid0 in self.disabled_strategies: continue
                    filtered_hits.append(hit)
                prim=self._primary_hit(filtered_hits)
                if not prim:
                    if any(h.get("strength")=="primary" for h in hits):
                        self.rejections.append({"date":d,"symbol":sym,"reason":"NO_ENABLED_PRIMARY_STRATEGY","strategies":[h.get("signal") for h in hits if h.get("strength")=="primary"]})
                    continue
                hits=filtered_hits
                sid=str(prim.get("signal")); signal_count+=1; signal_stats[sid]=signal_stats.get(sid,0)+1
                family=str(prim["family"])
                allowed=set(route.get("allowed_strategy_families",[])); conditional=set(route.get("conditional_strategy_families",[]))
                if family not in allowed and family not in conditional:
                    self.rejections.append({"date":d,"symbol":sym,"reason":"ROUTER_BLOCK","strategy":prim["signal"],"route_id":route["route_id"]}); continue
                score,breakdown=deterministic_score(hits,hist,market,sector)
                threshold=self.s.min_score+float(route.get("candidate_threshold_delta",0))
                if family in conditional: threshold += 3
                if score < threshold:
                    self.rejections.append({"date":d,"symbol":sym,"reason":"SCORE_BELOW_THRESHOLD","score":score,"threshold":threshold,"strategy":prim["signal"],"route_id":route["route_id"]}); continue
                info_sector = provider.sector_info(sym) if hasattr(provider, "sector_info") else {}
                sector_code = (info_sector or {}).get("code") if info_sector else None
                resonance = sector_resonance_filter(sym, sector_code, None, daily_candidates, d, self.provider)
                if resonance.get("sector_strength", 0) >= 2 or score >= 80 or resonance.get("reason") == "no_sector_data":
                    pass  # allow
                else:
                    self.rejections.append({"date": d, "symbol": sym, "reason": f"SECTOR_RESONANCE: {resonance.get('reason','?')}"})
                    continue
                stop_adj=self._stop_for_entry(hist,float(hist[-1]["close"]))
                adj_close=float(hist[-1]["close"])
                raw_b_today=raw_map_by_symbol.get(sym,{}).get(d)
                if raw_b_today and float(raw_b_today.get("close",0) or 0)>0 and adj_close>0:
                    raw_close=float(raw_b_today["close"])
                    stop_raw=raw_close * (stop_adj / adj_close)
                else:
                    stop_raw=stop_adj
                daily_candidates.append({"symbol":sym,"score":score,"breakdown":breakdown,"primary":prim,"hits":hits,"route":route,"market":market,"sector":sector,"sector_name":secinfo.get("name"),"stop":stop_raw,"recent_bars":hist[-80:]})

            if next_d and _in_entry_window:
                daily_candidates.sort(key=lambda x:(x["score"],float(x["sector"].get("sector_score",50))),reverse=True)
                max_new=int(self.cfg.defaults.get("trade_behavior",{}).get("max_new_positions_per_day",4))
                route_caps=[x["route"].get("max_new_positions_override") for x in daily_candidates if x["route"].get("max_new_positions_override") is not None]
                if route_caps: max_new=min(max_new,max(0,max(int(x) for x in route_caps)))
                room=max(0,self.s.max_positions-len(portfolio.positions)-sum(1 for o in pending if o.direction=="BUY"))
                slots=min(max_new,room)

                if self.s.llm_filter_enabled:
                    if not self.llm_filter: raise RuntimeError("llm_filter_enabled but no HistoricalLLMFilter was provided")
                    # Only ask the LLM about candidates that can realistically fill the
                    # remaining portfolio slots. Continue until slots are filled or the
                    # configured review cap is exhausted.
                    gated=[]
                    review_cap=min(len(daily_candidates),max(slots,int(self.s.llm_filter_top_n))) if slots>0 else 0
                    for x in daily_candidates[review_cap:]:
                        self.rejections.append({"date":d,"symbol":x["symbol"],"reason":"LLM_TOP_N_CUTOFF","score":x["score"],"strategy":x["primary"]["signal"]})
                    reviewed=0
                    batch_size=max(1,int(self.s.llm_filter_batch_size))
                    while reviewed < review_cap and len(gated) < slots:
                        chunk=daily_candidates[reviewed:min(review_cap,reviewed+batch_size)]
                        decisions=self.llm_filter.decide_batch(as_of=d,candidates=[(x["symbol"],x,x["recent_bars"]) for x in chunk]) if chunk else {}
                        for x in chunk:
                            dec=decisions.get(x["symbol"],{"decision":"ERROR","reasons_against":["LLM_MISSING_DECISION"]})
                            x["llm_filter"]=dec
                            decision=str(dec.get("decision") or "ERROR")
                            if decision=="ERROR":
                                self.rejections.append({"date":d,"symbol":x["symbol"],"reason":"LLM_FILTER_ERROR","score":x["score"],"strategy":x["primary"]["signal"],"llm":dec})
                                continue
                            if decision not in set(self.s.llm_filter_accept):
                                self.rejections.append({"date":d,"symbol":x["symbol"],"reason":f"LLM_{decision}","score":x["score"],"strategy":x["primary"]["signal"],"llm":dec})
                                continue
                            gated.append(x)
                            if len(gated)>=slots: break
                        reviewed += len(chunk)
                    daily_candidates=gated

                for idx, x in enumerate(daily_candidates[:slots]):
                    meta={"score_breakdown":x["breakdown"],"hits":x["hits"],"market":x["market"],"sector":x["sector"]}
                    if x.get("llm_filter"):
                        meta["llm_filter"]=x["llm_filter"]; meta["llm_decision"]=x["llm_filter"].get("decision")
                    sym = x["symbol"]
                    pat_id = x["primary"].get("pattern_id") or x["primary"]["signal"]
                    pat_ver = x["primary"].get("pattern_version", "1.0.0")
                    raw = f"{sym}|{d}|{next_d}|{pat_id}|{pat_ver}|{idx}"
                    rtid = hashlib.sha256(raw.encode()).hexdigest()[:16]
                    pending.append(PendingOrder(x["symbol"],"BUY",d,next_d,"ENTRY_SIGNAL",str(x["primary"]["signal"]),str(x["primary"]["family"]),float(x["score"]),str(x["route"]["route_id"]),x["sector_name"],float(x["stop"]),float(x["route"].get("position_multiplier",1.0)),0,meta,pattern_id=x["primary"].get("pattern_id") or x["primary"]["signal"],pattern_version=x["primary"].get("pattern_version","1.0.0"),regime_at_signal=x["market"].get("regime") or x["market"].get("market_regime"),regime_confidence_at_signal=x["market"].get("regime_confidence"),regime_data_quality_at_signal=x["market"].get("data_quality"),theme=x["sector"].get("sector") or x["sector_name"],theme_lifecycle=x["sector"].get("lifecycle") or x["sector"].get("sector_lifecycle"),theme_lifecycle_confidence=x["sector"].get("lifecycle_confidence"),theme_data_quality=x["sector"].get("data_quality"),signal_strength=x["primary"].get("strength"),round_trip_id=rtid))
                    self._log(d,"ENTRY_SIGNAL",symbol=x["symbol"],score=x["score"],strategy=x["primary"]["signal"],route_id=x["route"]["route_id"],execute_date=next_d,llm_decision=(x.get("llm_filter") or {}).get("decision"))

            equity=portfolio.equity(current_prices)
            equity_curve.append({"date":d,"equity":equity,"cash":portfolio.cash,"market_value":portfolio.market_value(current_prices),"positions":len(portfolio.positions),"market_regime":market.get("market_regime"),"active_universe":len(active_symbols)})

        last_date=dates[-1]
        if evaluation_window is None:
            for sym in list(portfolio.positions):
                if not ensure_symbol(sym): continue
                pos = portfolio.positions[sym]
                raw_bar = raw_map_by_symbol.get(sym, {}).get(last_date)
                if raw_bar:
                    sec_info = daily_sector_by_symbol.get(sym, ({"name": pos.sector}, {"sector_lifecycle":"unknown","lifecycle":"UNKNOWN"}))
                    sec_ctx = sec_info[1] if isinstance(sec_info, tuple) else {"sector_lifecycle":"unknown","lifecycle":"UNKNOWN"}
                    tr = portfolio.sell(symbol=sym, date=last_date, signal_date=last_date,
                        raw_price=float(raw_bar["close"]), reason="END_OF_BACKTEST",
                        cost_model=self.costs,
                        exit_regime=market.get("regime") if isinstance(market, dict) else None,
                        exit_theme=sec_ctx.get("sector") or pos.sector,
                        exit_theme_lifecycle=sec_ctx.get("lifecycle") or sec_ctx.get("sector_lifecycle"))
                    if tr:
                        self._log(last_date, "TRADE", trade=tr.to_dict())
            if equity_curve:
                equity_curve[-1]["equity"]=portfolio.equity(current_prices); equity_curve[-1]["cash"]=portfolio.cash; equity_curve[-1]["market_value"]=portfolio.market_value(current_prices); equity_curve[-1]["positions"]=0
        else:
            # Evaluation window active: no forced liquidation.
            # Record remaining portfolio positions, censored, and pending orders.
            _portfolio_positions = [pos.to_dict() for pos in portfolio.positions.values()]
            censored: list[dict[str, Any]] = []
            for sym, pos in list(portfolio.positions.items()):
                raw_bar = raw_map_by_symbol.get(sym, {}).get(last_date)
                current_price = float(raw_bar["close"]) if raw_bar else pos.entry_price
                censored.append({
                    "symbol": sym,
                    "entry_date": pos.entry_date,
                    "quantity": pos.quantity,
                    "current_price": current_price,
                    "reason": "OPEN_AT_OBSERVATION_END",
                })

        trades=[x.to_dict() for x in portfolio.trades]
        metrics=performance_metrics(equity_curve,trades,self.s.initial_cash)
        months=monthly_returns(equity_curve)
        target=float(self.cfg.defaults.get("performance_objective",{}).get("target_monthly_return",0.30))
        target_hits=sum(1 for x in months if float(x.get("return",0))>=target)
        bperiod=[b for b in benchmark if self.s.start_date<=b["date"]<=self.s.end_date]
        benchmark_return=(float(bperiod[-1]["close"])/float(bperiod[0]["close"])-1) if len(bperiod)>=2 and float(bperiod[0]["close"]) else 0.0
        metrics["starting_equity"] = float(self.s.initial_cash)
        metrics["benchmark_return"]=benchmark_return
        metrics["excess_return_vs_benchmark"]=float(metrics.get("total_return",0))-benchmark_return
        metrics["monthly_target"]=target; metrics["months_total"]=len(months); metrics["months_ge_target"]=target_hits
        metrics["months_ge_target_rate"]=target_hits/len(months) if months else 0.0
        metrics["best_month"]=max((float(x["return"]) for x in months),default=0.0); metrics["worst_month"]=min((float(x["return"]) for x in months),default=0.0)

        active_counts=[int(x.get("active_symbols",0)) for x in universe_daily]
        point_flags=[bool(x.get("point_in_time")) for x in universe_daily]
        self.data_quality["sector_mapping_sources"]=sector_sources
        checked=max(1,len(sector_symbols_checked))
        self.data_quality["sector_mapping_coverage"]=len(sector_symbols_mapped)/checked if sector_symbols_checked else 0.0
        self.data_quality["historical_sector_mapping_coverage"]=len(sector_symbols_historical)/checked if sector_symbols_checked else 0.0
        self.data_quality["historical_sector_membership_point_in_time"]=bool(sector_symbols_checked) and len(sector_symbols_historical)==len(sector_symbols_checked)
        report={
            "run_id":f"bt-{uuid.uuid4().hex[:12]}","created_at":datetime.now().astimezone().isoformat(),
            "settings":self.s.to_dict(),"metrics":metrics,"monthly_returns":months,"equity_curve":equity_curve,"trades":trades,
            "rejections":self.rejections,"events":self.logs,
            "by_strategy":grouped_trade_stats(trades,"strategy_id"),"by_family":grouped_trade_stats(trades,"strategy_family"),
            "by_route":grouped_trade_stats(trades,"route_id"),"by_sector":grouped_trade_stats(trades,"sector"),
            "by_market_regime":grouped_trade_stats(trades,"entry_market_regime"),"by_sector_strength":grouped_trade_stats(trades,"entry_sector_strength"),
            # v0.8: Regime × Pattern × Theme Lifecycle performance matrix
            "regime_pattern_matrix": regime_pattern_matrix(trades),
            "coverage":{
                "requested_symbols":len(seen_symbols),"tested_symbols":len(bars_by_symbol),"missing_symbols":len(invalid_symbols),
                "signal_count":signal_count,"deterministic_candidate_count":candidates_count,"candidate_count":candidates_count,
                "dynamic_universe_days":len(universe_daily),"active_universe_min":min(active_counts) if active_counts else 0,
                "active_universe_max":max(active_counts) if active_counts else 0,"active_universe_avg":(sum(active_counts)/len(active_counts)) if active_counts else 0.0,
            },
            "universe_daily":universe_daily,"sector_heat_daily":sector_heat_daily,
            "data_quality":{
                **self.data_quality,"provider_warnings":self.provider.warnings,
                "point_in_time_universe_all_days":bool(point_flags) and all(point_flags),
                "dynamic_universe_daily":bool(getattr(self.provider,"dynamic_universe_enabled",False)),
                "historical_market_context":"derived_from_multiple_benchmark_prices",
                "historical_sector_context":"derived_from_historical_sector_membership_and_sector_price_history_when_available",
                "present_day_market_health_used":False,"present_day_mainline_used":False,
            },
            "attribution_audit": batch_audit_attribution(trades),
        "methodology":{
                "signal_time":"daily_close","entry_execution":"next_trading_day_open","exit_signal_execution":"next_trading_day_open",
                "protective_stop":"intraday_daily_bar_low; T+1 enforced","lookahead_protection":True,"llm_used":bool(self.s.llm_filter_enabled),
                "decision_engine":"deterministic + historical LLM candidate gate" if self.s.llm_filter_enabled else "deterministic rule/scoring engine for reproducibility",
                "llm_filter_stats":self.llm_filter.stats() if self.llm_filter else None,
                "route_mode":self.s.route_mode,"sector_mode":self.s.sector_mode,"sector_relative_ranking":bool(self.s.sector_relative_ranking),"enabled_strategies":list(self.enabled_strategies),"disabled_strategies":list(self.disabled_strategies),"research_tag":self.s.research_tag,
                "market_benchmarks":benchmark_symbols,"universe_method":"daily_point_in_time_dynamic" if (bool(getattr(self.provider,"dynamic_universe_enabled",False)) and all(point_flags)) else "static_or_diagnostic_fallback",
            },
        }
        if evaluation_window is not None:
            report["evaluation_window"] = evaluation_window
            report["portfolio_positions"] = _portfolio_positions
            report["censored_positions"] = censored
            report["pending_orders"] = [o.to_dict() for o in pending]
        return report
