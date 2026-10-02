from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import RuntimeConfig
from ..strategy.signal_engine import DeterministicSignalEngine
from ..strategy.router import StrategyRouter
from .costs import AShareCostModel, locked_at_limit, price_limit_pct
from .data import HistoricalDataProvider
from .metrics import performance_metrics, monthly_returns, grouped_trade_stats
from .models import BacktestSettings, PendingOrder
from .portfolio import Portfolio
from .portfolio import Portfolio
from .regime import market_context_from_benchmarks, sector_context_from_history
from .scoring import deterministic_score
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

    def run(self, symbols: list[str]) -> dict[str,Any]:
        benchmark_symbols=list(dict.fromkeys([self.s.benchmark, *[str(x) for x in self.cfg.defaults.get("benchmarks",{}).values()]]))
        benchmark_series={sym:self.provider.bars(sym,count=max(900,self.s.warmup_bars+550)) for sym in benchmark_symbols}
        benchmark=benchmark_series.get(self.s.benchmark) or next((v for v in benchmark_series.values() if v),[])
        if not benchmark:
            raise RuntimeError(f"benchmark bars unavailable: {self.s.benchmark}")
        dates=[b["date"] for b in benchmark if self.s.start_date <= b["date"] <= self.s.end_date]
        if not dates:
            raise RuntimeError("no benchmark trading dates in requested range")

        bars_by_symbol: dict[str,list[dict[str,Any]]]={}
        map_by_symbol: dict[str,dict[str,dict[str,Any]]]={}
        sector_by_symbol: dict[str,dict[str,Any]]={}
        sector_bars: dict[str,list[dict[str,Any]]]={}
        valid=[]
        required_start_index=max(0,next((i for i,b in enumerate(benchmark) if b["date"]>=self.s.start_date),0)-self.s.warmup_bars)
        oldest_needed=benchmark[required_start_index]["date"]
        for sym in symbols:
            bars=self.provider.bars(sym,count=max(900,self.s.warmup_bars+550))
            if len(bars)<60 or bars[-1]["date"] < self.s.start_date:
                self.data_quality["missing_bars"].append(sym); continue
            bars_by_symbol[sym]=bars; map_by_symbol[sym]=_bar_map(bars); valid.append(sym)
            sec=self.provider.sector_info(sym); sector_by_symbol[sym]=sec
            code=sec.get("code")
            if code and code not in sector_bars:
                sector_bars[code]=self.provider.sector_bars(str(code))
        symbols=valid
        sector_sources: dict[str,int]={}
        for info in sector_by_symbol.values():
            src=str(info.get("source") or "unknown"); sector_sources[src]=sector_sources.get(src,0)+1
        self.data_quality["sector_mapping_sources"]=sector_sources
        self.data_quality["historical_sector_membership_point_in_time"]=bool(sector_sources) and all(src.startswith("historical") for src in sector_sources)
        portfolio=Portfolio(self.s.initial_cash)
        pending: list[PendingOrder]=[]
        equity_curve=[]
        candidates_count=0
        signal_count=0
        current_prices: dict[str,float]={}
        regime_by_date: dict[str,str]={}
        route_stats: dict[str,int]={}
        signal_stats: dict[str,int]={}
        executed_entries_today: dict[str,int]={}

        for di,d in enumerate(dates):
            next_d=self._next_date(dates,di)
            market=market_context_from_benchmarks(benchmark_series,d)
            regime_by_date[d]=str(market.get("market_regime"))
            # Update prices for mark-to-market.
            for sym in symbols:
                bar=map_by_symbol[sym].get(d)
                if bar: current_prices[sym]=float(bar["close"])

            # 1) Execute pending orders scheduled for today at today's open.
            todays=[o for o in pending if o.execute_date==d]
            pending=[o for o in pending if o.execute_date!=d]
            for o in todays:
                bar=map_by_symbol.get(o.symbol,{}).get(d)
                prev_hist=[x for x in bars_by_symbol.get(o.symbol,[]) if x["date"]<d]
                if not bar or not prev_hist:
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"NO_EXECUTION_BAR","order":o.to_dict()}); continue
                b={**bar,"symbol":o.symbol}; prev_close=float(prev_hist[-1]["close"])
                if self.s.block_open_at_limit and locked_at_limit(b,prev_close,o.direction):
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"LOCKED_AT_PRICE_LIMIT","order":o.to_dict()}); continue
                if o.direction=="SELL":
                    tr=portfolio.sell(symbol=o.symbol,date=d,signal_date=o.created_date,raw_price=float(bar["open"]),reason=o.reason,cost_model=self.costs)
                    if tr: self._log(d,"TRADE",trade=tr.to_dict())
                    continue
                if o.symbol in portfolio.positions:
                    continue
                if not self._eligible(o.symbol,d):
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"NOT_POINT_IN_TIME_ELIGIBLE","order":o.to_dict()}); continue
                if len(portfolio.positions)>=self.s.max_positions:
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"MAX_POSITIONS","order":o.to_dict()}); continue
                equity=portfolio.equity(current_prices); mv=portfolio.market_value(current_prices)
                raw=float(bar["open"]); stop=float(o.stop_price or raw*.95)
                qty=portfolio.size_for_risk(equity=equity,price=self.costs.slip_price(raw,"BUY"),stop=stop,
                    risk_per_trade=self.s.risk_per_trade,max_single=self.s.max_single_position,max_total=self.s.max_total_position,
                    current_market_value=mv,multiplier=o.route_multiplier,lot=self.s.position_round_lot)
                if o.requested_quantity>0: qty=min(qty,o.requested_quantity)
                if qty<self.s.position_round_lot:
                    self.rejections.append({"date":d,"symbol":o.symbol,"reason":"RISK_SIZE_ZERO","order":o.to_dict()}); continue
                tr=portfolio.buy(symbol=o.symbol,date=d,signal_date=o.created_date,raw_price=raw,quantity=qty,stop_price=stop,
                    strategy_id=str(o.strategy_id),strategy_family=str(o.strategy_family),score=float(o.score or 0),route_id=o.route_id,sector=o.sector,
                    cost_model=self.costs,meta=o.signal_meta)
                if tr:
                    executed_entries_today[d]=executed_entries_today.get(d,0)+1; self._log(d,"TRADE",trade=tr.to_dict())

            # 2) Intraday protective stop / position lifecycle. T+1: entry date cannot sell.
            for sym,pos in list(portfolio.positions.items()):
                bar=map_by_symbol.get(sym,{}).get(d)
                if not bar: continue
                pos.highest_price=max(pos.highest_price,float(bar["high"])); pos.lowest_price=min(pos.lowest_price or pos.entry_price,float(bar["low"]));
                if d>pos.entry_date: pos.holding_days += 1
                if d>pos.entry_date and float(bar["low"]) <= pos.stop_price:
                    raw=min(float(bar["open"]),pos.stop_price) if float(bar["open"])<pos.stop_price else pos.stop_price
                    tr=portfolio.sell(symbol=sym,date=d,signal_date=d,raw_price=raw,reason="STOP_LOSS",cost_model=self.costs)
                    if tr: self._log(d,"TRADE",trade=tr.to_dict())

            # 3) Close-confirmed exits -> next open; then close-confirmed entries.
            for sym,pos in list(portfolio.positions.items()):
                hist=[x for x in bars_by_symbol[sym] if x["date"]<=d]
                if len(hist)<60: continue
                secinfo=sector_by_symbol.get(sym,{})
                sbars=sector_bars.get(str(secinfo.get("code")),[]) if secinfo.get("code") else []
                sector=self._sector_context(sbars,d,secinfo.get("name"))
                hits=self.signal_engine.scan(hist,market_regime=str(market.get("market_regime")),sector_strength=str(sector.get("sector_strength")))
                ex=self._exit_hit(hits)
                reason=None
                if ex: reason=f"SIGNAL:{ex['signal']}"
                elif pos.holding_days>=self.s.max_holding_days: reason="MAX_HOLDING_DAYS"
                if reason and next_d and not any(o.symbol==sym and o.direction=="SELL" for o in pending):
                    pending.append(PendingOrder(sym,"SELL",d,next_d,reason,pos.strategy_id,pos.strategy_family,pos.score,pos.route_id,pos.sector))
                    self._log(d,"EXIT_SIGNAL",symbol=sym,reason=reason,execute_date=next_d)

            # Entry scanning. Cap is intentionally applied after ranking all candidates for the day.
            daily_candidates=[]
            for sym in symbols:
                if not self._eligible(sym,d): continue
                if sym in portfolio.positions or any(o.symbol==sym and o.direction=="BUY" for o in pending): continue
                hist=[x for x in bars_by_symbol[sym] if x["date"]<=d]
                if len(hist)<60 or hist[-1]["date"]!=d: continue
                secinfo=sector_by_symbol.get(sym,{})
                sbars=sector_bars.get(str(secinfo.get("code")),[]) if secinfo.get("code") else []
                if not sbars and secinfo.get("code"):
                    self.data_quality["sector_history_missing"].append(str(secinfo.get("code")))
                sector=self._sector_context(sbars,d,secinfo.get("name"))
                route=self._route(market,sector)
                route_stats[route["route_id"]]=route_stats.get(route["route_id"],0)+1
                hits=self.signal_engine.scan(hist,market_regime=str(market.get("market_regime")),sector_strength=str(sector.get("sector_strength")))
                filtered_hits=[]
                for hit in hits:
                    sid0=str(hit.get("signal"))
                    if hit.get("strength")=="primary":
                        if self.enabled_strategies and sid0 not in self.enabled_strategies:
                            continue
                        if sid0 in self.disabled_strategies:
                            continue
                    filtered_hits.append(hit)
                prim=self._primary_hit(filtered_hits)
                if not prim:
                    if any(h.get("strength")=="primary" for h in hits):
                        self.rejections.append({"date":d,"symbol":sym,"reason":"NO_ENABLED_PRIMARY_STRATEGY","strategies":[h.get("signal") for h in hits if h.get("strength")=="primary"]})
                    continue
                hits=filtered_hits
                sid=str(prim.get("signal"))
                signal_count+=1; signal_stats[sid]=signal_stats.get(sid,0)+1
                family=str(prim["family"])
                allowed=set(route.get("allowed_strategy_families",[])); conditional=set(route.get("conditional_strategy_families",[]))
                if family not in allowed and family not in conditional:
                    self.rejections.append({"date":d,"symbol":sym,"reason":"ROUTER_BLOCK","strategy":prim["signal"],"route_id":route["route_id"]}); continue
                score,breakdown=deterministic_score(hits,hist,market,sector)
                threshold=self.s.min_score+float(route.get("candidate_threshold_delta",0))
                if family in conditional: threshold += 3
                if score < threshold:
                    self.rejections.append({"date":d,"symbol":sym,"reason":"SCORE_BELOW_THRESHOLD","score":score,"threshold":threshold,"strategy":prim["signal"],"route_id":route["route_id"]}); continue
                candidates_count+=1
                stop=self._stop_for_entry(hist,float(hist[-1]["close"]))
                daily_candidates.append({"symbol":sym,"score":score,"breakdown":breakdown,"primary":prim,"hits":hits,"route":route,"market":market,"sector":sector,"sector_name":secinfo.get("name"),"stop":stop,"recent_bars":hist[-80:]})

            if next_d:
                daily_candidates.sort(key=lambda x:(x["score"],float(x["sector"].get("sector_score",50))),reverse=True)
                max_new=int(self.cfg.defaults.get("trade_behavior",{}).get("max_new_positions_per_day",4))
                route_caps=[x["route"].get("max_new_positions_override") for x in daily_candidates if x["route"].get("max_new_positions_override") is not None]
                if route_caps: max_new=min(max_new,max(0,max(int(x) for x in route_caps)))
                room=max(0,self.s.max_positions-len(portfolio.positions)-sum(1 for o in pending if o.direction=="BUY"))
                target_accept=min(max_new,room)

                if self.s.llm_filter_enabled:
                    if not self.llm_filter:
                        raise RuntimeError("llm_filter_enabled but no HistoricalLLMFilter was provided")
                    gated=[]
                    top_n=min(len(daily_candidates),max(1,int(self.s.llm_filter_top_n)))
                    batch=max(1,int(self.s.llm_filter_batch_size))
                    reviewed=0
                    # Review only as many ranked candidates as needed to fill actual portfolio capacity.
                    # If the gate rejects some, continue down the ranking until capacity is filled or top_n is exhausted.
                    while reviewed < top_n and len(gated) < target_accept:
                        chunk=daily_candidates[reviewed:min(top_n,reviewed+batch)]
                        decisions=self.llm_filter.decide_batch(as_of=d,candidates=[(x["symbol"],x,x["recent_bars"]) for x in chunk]) if chunk else {}
                        for x in chunk:
                            dec=decisions.get(x["symbol"],{
                                "decision":"ERROR","confidence":0,"reasons_for":[],
                                "reasons_against":["LLM_MISSING_DECISION"],"risk_flags":["MISSING_DECISION"]
                            })
                            x["llm_filter"]=dec
                            decision=str(dec.get("decision") or "ERROR")
                            if decision == "ERROR":
                                self.rejections.append({"date":d,"symbol":x["symbol"],"reason":"LLM_ERROR_EXCLUDED","score":x["score"],"strategy":x["primary"]["signal"],"llm":dec})
                                continue
                            if decision not in set(self.s.llm_filter_accept):
                                self.rejections.append({"date":d,"symbol":x["symbol"],"reason":f"LLM_{decision}","score":x["score"],"strategy":x["primary"]["signal"],"llm":dec})
                                continue
                            gated.append(x)
                            if len(gated) >= target_accept:
                                break
                        reviewed += len(chunk)
                    for x in daily_candidates[reviewed:top_n]:
                        self.rejections.append({"date":d,"symbol":x["symbol"],"reason":"LLM_NOT_REVIEWED_CAPACITY_FILLED","score":x["score"],"strategy":x["primary"]["signal"]})
                    for x in daily_candidates[top_n:]:
                        self.rejections.append({"date":d,"symbol":x["symbol"],"reason":"LLM_TOP_N_CUTOFF","score":x["score"],"strategy":x["primary"]["signal"]})
                    daily_candidates=gated

                for x in daily_candidates[:target_accept]:
                    meta={"score_breakdown":x["breakdown"],"hits":x["hits"],"market":x["market"],"sector":x["sector"]}
                    if x.get("llm_filter"):
                        meta["llm_filter"]=x["llm_filter"]; meta["llm_decision"]=x["llm_filter"].get("decision")
                    pending.append(PendingOrder(x["symbol"],"BUY",d,next_d,"ENTRY_SIGNAL",str(x["primary"]["signal"]),str(x["primary"]["family"]),float(x["score"]),str(x["route"]["route_id"]),x["sector_name"],float(x["stop"]),float(x["route"].get("position_multiplier",1.0)),0,meta))
                    self._log(d,"ENTRY_SIGNAL",symbol=x["symbol"],score=x["score"],strategy=x["primary"]["signal"],route_id=x["route"]["route_id"],execute_date=next_d,llm_decision=(x.get("llm_filter") or {}).get("decision"))

            equity=portfolio.equity(current_prices)
            equity_curve.append({"date":d,"equity":equity,"cash":portfolio.cash,"market_value":portfolio.market_value(current_prices),"positions":len(portfolio.positions),"market_regime":market.get("market_regime")})

        # Liquidate remaining positions at final available close, marked as end-of-test.
        last_date=dates[-1]
        for sym in list(portfolio.positions):
            bar=map_by_symbol.get(sym,{}).get(last_date)
            if bar:
                tr=portfolio.sell(symbol=sym,date=last_date,signal_date=last_date,raw_price=float(bar["close"]),reason="END_OF_BACKTEST",cost_model=self.costs)
                if tr: self._log(last_date,"TRADE",trade=tr.to_dict())
        if equity_curve:
            equity_curve[-1]["equity"]=portfolio.equity(current_prices); equity_curve[-1]["cash"]=portfolio.cash; equity_curve[-1]["market_value"]=portfolio.market_value(current_prices); equity_curve[-1]["positions"]=0

        trades=[x.to_dict() for x in portfolio.trades]
        metrics=performance_metrics(equity_curve,trades,self.s.initial_cash)
        months=monthly_returns(equity_curve)
        target=float(self.cfg.defaults.get("performance_objective",{}).get("target_monthly_return",0.30))
        target_hits=sum(1 for x in months if float(x.get("return",0))>=target)
        bperiod=[b for b in benchmark if self.s.start_date<=b["date"]<=self.s.end_date]
        benchmark_return=(float(bperiod[-1]["close"])/float(bperiod[0]["close"])-1) if len(bperiod)>=2 and float(bperiod[0]["close"]) else 0.0
        metrics["benchmark_return"]=benchmark_return
        metrics["excess_return_vs_benchmark"]=float(metrics.get("total_return",0))-benchmark_return
        metrics["monthly_target"]=target
        metrics["months_total"]=len(months)
        metrics["months_ge_target"]=target_hits
        metrics["months_ge_target_rate"]=target_hits/len(months) if months else 0.0
        metrics["best_month"]=max((float(x["return"]) for x in months),default=0.0)
        metrics["worst_month"]=min((float(x["return"]) for x in months),default=0.0)
        report={
            "run_id":f"bt-{uuid.uuid4().hex[:12]}","created_at":datetime.now().astimezone().isoformat(),
            "settings":self.s.to_dict(),"metrics":metrics,"monthly_returns":months,"equity_curve":equity_curve,"trades":trades,
            "rejections":self.rejections,"events":self.logs,
            "by_strategy":grouped_trade_stats(trades,"strategy_id"),"by_family":grouped_trade_stats(trades,"strategy_family"),
            "by_route":grouped_trade_stats(trades,"route_id"),"by_sector":grouped_trade_stats(trades,"sector"),
            "by_market_regime":grouped_trade_stats(trades,"entry_market_regime"),"by_sector_strength":grouped_trade_stats(trades,"entry_sector_strength"),
            "coverage":{"requested_symbols":len(valid)+len(self.data_quality["missing_bars"]),"tested_symbols":len(valid),"missing_symbols":len(self.data_quality["missing_bars"]),"signal_count":signal_count,"deterministic_candidate_count":candidates_count,"candidate_count":candidates_count},
            "data_quality":{
                **self.data_quality,"provider_warnings":self.provider.warnings,
                "historical_market_context":"derived_from_multiple_benchmark_prices",
                "historical_sector_context":"derived_from_sector_price_history_when_available",
                "present_day_market_health_used":False,"present_day_mainline_used":False,
            },
            "methodology":{
                "signal_time":"daily_close","entry_execution":"next_trading_day_open","exit_signal_execution":"next_trading_day_open",
                "protective_stop":"intraday_daily_bar_low; T+1 enforced","lookahead_protection":True,"llm_used":bool(self.s.llm_filter_enabled),
                "decision_engine":"deterministic + historical LLM candidate gate" if self.s.llm_filter_enabled else "deterministic rule/scoring engine for reproducibility",
                "llm_filter_stats":self.llm_filter.stats() if self.llm_filter else None,
                "route_mode":self.s.route_mode,"sector_mode":self.s.sector_mode,"enabled_strategies":list(self.enabled_strategies),"disabled_strategies":list(self.disabled_strategies),"research_tag":self.s.research_tag,
                "market_benchmarks":benchmark_symbols,
            },
        }
        return report
