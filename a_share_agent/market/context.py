from __future__ import annotations

from statistics import mean
from typing import Any

from .features import (
    FEATURE_VERSION,
    compute_breadth,
    compute_concentration,
    compute_expansion,
    compute_leaders,
    compute_persistence,
    compute_turnover_share,
)
from ..backtest.regime import (
    REGIME_7STATE as _REGIME_7STATE,
    LIFECYCLE_7STATE as _LIFECYCLE_7STATE,
    determine_regime_7state,
    determine_lifecycle_7state,
    compute_regime_confidence,
    compute_data_quality,
)


def _bars(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("bars", "data", "kline", "klines", "items"):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def _close(bar: dict[str, Any]) -> float | None:
    for key in ("close", "c", "收盘"):
        if key in bar:
            try: return float(bar[key])
            except (TypeError, ValueError): return None
    return None


class MarketContextBuilder:
    def __init__(self, config: RuntimeConfig, mcp: MCPInvoker):
        self.config, self.mcp = config, mcp
        self._last_regime: str | None = None
        self._last_leader_sectors: list[str] | None = None

    def build(self) -> dict[str, Any]:
        bench_cfg = self.config.defaults.get("benchmarks", {})
        benchmarks = []
        up = down = 0
        missing = []
        for _, symbol in bench_cfg.items():
            try:
                raw = self.mcp.invoke("mcp_intel_tdx_kline", symbol=symbol, period="D", count=40)
                bs = _bars(raw)
                if not bs:
                    raw = self.mcp.invoke("mcp_intel_fetch_kline", symbol=symbol, period="D", count=40)
                    bs = _bars(raw)
            except Exception:
                raw = self.mcp.invoke("mcp_intel_fetch_kline", symbol=symbol, period="D", count=40)
                bs = _bars(raw)
            closes = [c for c in (_close(x) for x in bs) if c is not None]
            if len(closes) < 20:
                benchmarks.append({"symbol": symbol, "state": "unknown", "close": closes[-1] if closes else None, "ma20": None, "ma20_slope": None})
                missing.append(f"benchmark:{symbol}")
                continue
            ma20 = mean(closes[-20:])
            prev = mean(closes[-25:-5]) if len(closes) >= 25 else mean(closes[:20])
            slope = ma20 - prev
            state = "up" if closes[-1] > ma20 and slope > 0 else "down" if closes[-1] < ma20 and slope < 0 else "mixed"
            up += state == "up"; down += state == "down"
            benchmarks.append({"symbol": symbol, "state": state, "close": closes[-1], "ma20": ma20, "ma20_slope": slope})
        try:
            health = self.mcp.invoke("mcp_intel_fetch_market_health")
        except Exception:
            health = {"blowup_rate": 0.0, "limit_up": 0, "limit_down": 0, "total": 0, "zdt": 0}
        try:
            ladder = self.mcp.invoke("mcp_intel_get_limitup_ladder", date=None, min_streak=1)
        except Exception:
            ladder = {"total": 0, "stocks": []}
        try:
            lanes = self.mcp.invoke("mcp_intel_get_mainline_lanes", top_n=5)
        except Exception:
            lanes = {"total": 0, "lanes": []}
        blowup = float(health.get("blowup_rate", 0.0)) if isinstance(health, dict) else 0.0
        weak_blowup = float(self.config.defaults.get("market_filter", {}).get("weak_blowup_rate", .45))
        if down >= 2 or blowup >= weak_blowup:
            regime, gate, trend = "risk_off", "block_short_beta", "down"
        elif up >= 2:
            regime, gate, trend = "risk_on", "allow", "up"
        else:
            regime, gate, trend = "neutral", "reduce", "range"
        if isinstance(health, dict):
            lup = float(health.get("limit_up", 0) or 0); ldn = float(health.get("limit_down", 0) or 0)
            sentiment = "strong" if lup > max(20, ldn*3) and blowup < .30 else "weak" if ldn > lup or blowup > .40 else "neutral"
        else:
            sentiment = "unknown"
        phase = "hot" if sentiment == "strong" and regime == "risk_on" else "panic" if sentiment == "weak" and regime == "risk_off" else "warming" if sentiment == "strong" else "unknown"

        # ── P3: Fetch sector & breadth data ──────────────────────────
        sector_raw = {"sectors": [], "total_turnover": 0}
        try:
            sector_raw = self.mcp.invoke("mcp_intel_get_sector_strength")
        except Exception:
            pass
        breadth_raw = {"stocks": []}
        try:
            breadth_raw = self.mcp.invoke("mcp_intel_fetch_market_breadth")
        except Exception:
            pass

        # ── Compute features ─────────────────────────────────────────
        sectors_list = sector_raw.get("sectors", [])
        stocks_list = breadth_raw.get("stocks", [])

        breadth = compute_breadth(stocks_list)
        leaders = compute_leaders(
            sectors_list,
            top_n=self.config.defaults.get("features", {}).get("leaders_top_n", 5),
            previous_top=self._last_leader_sectors,
        )
        turnover_share = compute_turnover_share(
            sectors_list,
            top_n=self.config.defaults.get("features", {}).get("turnover_top_n", 3),
        )
        expansion_threshold = float(
            self.config.defaults.get("features", {}).get("expansion_threshold", 60.0)
        )
        expansion = compute_expansion(sectors_list, threshold=expansion_threshold)
        concentration = compute_concentration(
            sectors_list,
            value_key=self.config.defaults.get("features", {}).get("concentration_value_key", "turnover"),
        )

        # ── 7-state regime ───────────────────────────────────────────
        # Use first valid benchmark for index price action
        index_ret5 = 0.0
        index_ret20 = 0.0
        index_dd20 = 0.0
        index_close = None
        index_ma20 = None
        index_ma_slope = 0.0
        for bm in benchmarks:
            if bm.get("close") and bm.get("state") != "unknown":
                index_close = bm["close"]
                index_ma20 = bm.get("ma20")
                index_ma_slope = bm.get("ma20_slope", 0.0)
                break
        has_index_data = index_close is not None

        regime_7 = determine_regime_7state(
            index_trend=trend,
            index_ma_slope=index_ma_slope,
            close_vs_ma=(index_close / index_ma20 - 1) if (index_close and index_ma20 and index_ma20 != 0) else 0.0,
            ret5=index_ret5,
            ret20=index_ret20,
            drawdown20=index_dd20,
            breadth_ratio=breadth["ratio"],
            adv_decline_ratio=(breadth["advancing"] / breadth["declining"]) if breadth.get("declining", 0) > 0 else None,
            new_high_count=int(health.get("limit_up", 0)),
            new_low_count=int(health.get("limit_down", 0)),
            vol_estimate=blowup,
        )

        persistence = compute_persistence(
            regime_7["regime"],
            previous_regimes=self._last_regime,
        )

        # ── 7-state lifecycle ────────────────────────────────────────
        lifecycle_7 = determine_lifecycle_7state(
            breadth_ratio=breadth["ratio"],
            leader_count=len(leaders),
            turnover_share=turnover_share,
            persistence=persistence,
            expansion=expansion,
            concentration=concentration,
        )

        # ── Confidence & quality ─────────────────────────────────────
        has_breadth_data = len(stocks_list) > 0
        has_sector_data = len(sectors_list) > 0
        regime_conf = compute_regime_confidence(
            regime_7,
            has_breadth=has_breadth_data,
            has_nh_nl=isinstance(health, dict) and health.get("limit_up") is not None,
            has_vol=blowup > 0,
        )
        data_quality = compute_data_quality(
            has_index_data=has_index_data,
            breadth_coverage=(breadth["total"] / max(self.config.defaults.get("features", {}).get("expected_stock_count", 5000), 1)),
            has_sector_data=has_sector_data,
        )

        # Save state for next invocation
        self._last_regime = regime_7["regime"]
        self._last_leader_sectors = [l["sector"] for l in leaders]

        # ── Assemble output ──────────────────────────────────────────
        now_iso = now_shanghai().isoformat()
        dq = {"state": "degraded" if missing else "ok", "missing": missing, "conflicts": []}
        if data_quality["state"] != "ok":
            dq = {
                "state": data_quality["state"],
                "reasons": data_quality["reasons"],
                "coverage": data_quality["coverage"],
                "missing": missing,
                "conflicts": [],
            }

        return {
            "as_of": now_iso,
            "trading_day": True,
            "session": None,
            "market_regime": regime,
            "market_trend": trend,
            "sentiment_state": sentiment,
            "sentiment_phase": phase,
            "benchmarks": benchmarks,
            "market_health": health,
            "limitup_ladder": ladder,
            "mainline_lanes": lanes,
            "market_gate": gate,
            "data_quality": dq,
            # P3 new fields
            "regime": regime_7["regime"],
            "regime_confidence": regime_conf,
            "theme_lifecycle": lifecycle_7["lifecycle"],
            "theme_lifecycle_confidence": lifecycle_7["confidence"],
            "breadth": breadth,
            "leaders": leaders,
            "turnover_share": turnover_share,
            "persistence": persistence,
            "expansion": expansion,
            "concentration": concentration,
            "feature_version": FEATURE_VERSION,
            "context_version": "0.8.0",
        }

    def compute_features_historical(
        self,
        *,
        sectors_list: list[dict[str, Any]],
        stocks_list: list[dict[str, Any]],
        index_trend: str,
        index_ma_slope: float,
        close_vs_ma: float,
        ret5: float,
        ret20: float,
        drawdown20: float,
        breadth_ratio: float | None,
        adv_decline_ratio: float | None,
        new_high_count: int | None,
        new_low_count: int | None,
        vol_estimate: float | None,
        previous_regime: str | None = None,
        previous_top_sectors: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return identical feature output to build() given the same inputs.

        Pure historical adapter for parity testing.  Uses no MCP — all data
        is passed explicitly.
        """
        breadth = compute_breadth(stocks_list)
        leaders = compute_leaders(
            sectors_list,
            top_n=self.config.defaults.get("features", {}).get("leaders_top_n", 5),
            previous_top=previous_top_sectors,
        )
        turnover_share = compute_turnover_share(
            sectors_list,
            top_n=self.config.defaults.get("features", {}).get("turnover_top_n", 3),
        )
        expansion_threshold = float(
            self.config.defaults.get("features", {}).get("expansion_threshold", 60.0)
        )
        expansion = compute_expansion(sectors_list, threshold=expansion_threshold)
        concentration = compute_concentration(
            sectors_list,
            value_key=self.config.defaults.get("features", {}).get("concentration_value_key", "turnover"),
        )

        regime_7 = determine_regime_7state(
            index_trend=index_trend,
            index_ma_slope=index_ma_slope,
            close_vs_ma=close_vs_ma,
            ret5=ret5,
            ret20=ret20,
            drawdown20=drawdown20,
            breadth_ratio=breadth_ratio,
            adv_decline_ratio=adv_decline_ratio,
            new_high_count=new_high_count,
            new_low_count=new_low_count,
            vol_estimate=vol_estimate,
        )

        persistence = compute_persistence(
            regime_7["regime"],
            previous_regimes=previous_regime,
        )

        lifecycle_7 = determine_lifecycle_7state(
            breadth_ratio=breadth_ratio,
            leader_count=len(leaders),
            turnover_share=turnover_share,
            persistence=persistence,
            expansion=expansion,
            concentration=concentration,
        )

        regime_conf = compute_regime_confidence(
            regime_7,
            has_breadth=(len(stocks_list) > 0),
            has_nh_nl=(new_high_count is not None),
            has_vol=(vol_estimate is not None and vol_estimate > 0),
        )
        data_quality = compute_data_quality(
            has_index_data=(index_trend != "unknown"),
            breadth_coverage=(breadth["total"] / max(self.config.defaults.get("features", {}).get("expected_stock_count", 5000), 1)) if breadth["total"] > 0 else 0.0,
            has_sector_data=(len(sectors_list) > 0),
        )

        return {
            "regime": regime_7["regime"],
            "regime_confidence": regime_conf,
            "theme_lifecycle": lifecycle_7["lifecycle"],
            "theme_lifecycle_confidence": lifecycle_7["confidence"],
            "breadth": breadth,
            "leaders": leaders,
            "turnover_share": turnover_share,
            "persistence": persistence,
            "expansion": expansion,
            "concentration": concentration,
            "data_quality": data_quality,
            "feature_version": FEATURE_VERSION,
        }
