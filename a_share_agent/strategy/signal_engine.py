from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any
from .pattern_registry import PatternRegistry, PatternSpec


def _f(x: Any, default: float = 0.0) -> float:
    try: return float(x)
    except (TypeError, ValueError): return default


def sma(xs: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    if n <= 0: return out
    acc = 0.0
    for i, v in enumerate(xs):
        acc += v
        if i >= n: acc -= xs[i-n]
        if i >= n-1: out[i] = acc/n
    return out


def ema(xs: list[float], n: int) -> list[float]:
    if not xs: return []
    a = 2/(n+1); out=[xs[0]]
    for v in xs[1:]: out.append(a*v + (1-a)*out[-1])
    return out


def crossed_up(a: list[float | None], b: list[float | None], i: int) -> bool:
    if i <= 0 or a[i] is None or b[i] is None or a[i-1] is None or b[i-1] is None: return False
    return a[i] > b[i] and a[i-1] <= b[i-1]


@dataclass
class SignalHit:
    signal: str
    family: str
    strength: str
    evidence: dict[str, Any]
    # v0.8: pattern identity for traceability
    pattern_id: str = ""
    pattern_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal, "family": self.family,
            "strength": self.strength, "evidence": self.evidence,
            "pattern_id": self.pattern_id or self.signal,
            "pattern_version": self.pattern_version,
        }


class DeterministicSignalEngine:
    """Computable v5 pattern detector.

    It detects evidence only. It does not claim win probability and it does not place
    orders. Thresholds follow the v5 SKILL defaults and should be backtested before
    production changes.
    """
    def _limitup_gene(self, bars: list[dict[str, Any]]) -> dict[str, Any]:
        """Check for limit-up gene in last 20 bars.

        Returns: {"has_gene": bool, "days_ago": int|None, "pct": float|None, "volume_ratio": float|None}
        """
        limit = min(20, len(bars) - 1)
        recent = bars[-limit:] if limit > 0 else bars
        for i in range(len(recent) - 1, -1, -1):
            bar = recent[i]
            pct = float(bar.get("pct", 0))
            vol = float(bar.get("volume", 0))
            if pct >= 9.5:
                vv20 = sum(float(b.get("volume", 0)) for b in bars[-21:-1]) / 20 if len(bars) > 21 else 1
                vol_ratio = vol / vv20 if vv20 else 1
                return {"has_gene": True, "days_ago": len(recent) - 1 - i, "pct": pct, "volume_ratio": round(vol_ratio, 2)}
        return {"has_gene": False, "days_ago": None, "pct": None, "volume_ratio": None}

    def _rebound_candidate(self, bars: list[dict[str, Any]], **context: Any) -> dict[str, Any]:
        """Detect a stock that has declined significantly and shows early signs of bottoming.

        Criteria (v4-inspired):
        - At least 15% drop from 20-bar high
        - Low volume on recent bars (selling exhaustion)
        - Small candle bodies near the low (doji / hammer-like)
        - Not in a confirmed uptrend (avoid false positives)
        """
        if len(bars) < 25:
            return None
        c = [float(x.get("close", 0)) for x in bars]
        h = [float(x.get("high", 0)) for x in bars]
        l = [float(x.get("low", 0)) for x in bars]
        o = [float(x.get("open", 0)) for x in bars]
        v = [float(x.get("volume", 0)) for x in bars]
        i = len(c) - 1

        high20 = max(h[-20:])
        drop_pct = (high20 - c[i]) / high20 if high20 else 0
        if drop_pct < 0.15:
            return None

        # Volume exhaustion: recent 3-bar avg volume < 20-bar avg volume
        v20_avg = sum(v[-20:]) / 20 if len(v) >= 20 else 1
        v3_avg = sum(v[-3:]) / 3 if len(v) >= 3 else 1
        vol_exhaustion = v3_avg < 0.7 * v20_avg if v20_avg else True

        # Small candle body near low (doji / hammer): body < 30% of range
        body = abs(c[i] - o[i])
        rng = max(h[i] - l[i], 1e-9)
        small_body = body / rng < 0.30 if rng > 0 else False

        # Consecutive decline candles: at least 2 of last 4 are red
        reds = sum(1 for j in range(max(0, i - 3), i + 1) if c[j] < o[j])
        decline_cluster = reds >= 2

        if (vol_exhaustion or small_body) and decline_cluster:
            return {
                "signal": "rebound_candidate",
                "family": "rebound_reversal",
                "strength": "confirmation",
                "evidence": {
                    "drop_pct": round(drop_pct, 4),
                    "volume_exhaustion": vol_exhaustion,
                    "small_body_near_low": small_body,
                    "decline_cluster": decline_cluster,
                },
                "pattern_id": "rebound_candidate",
                "pattern_version": "1.0.0",
            }
        return None

    def _rebound_confirmation(self, bars: list[dict[str, Any]], **context: Any) -> dict[str, Any]:
        """Detect confirmation of a rebound after a decline.

        Criteria:
        - Price breaks above MA5
        - Higher close than previous bar
        - Volume at least 80% of MA5 volume
        - Previously showed drops (rebound context)
        """
        if len(bars) < 25:
            return None
        c = [float(x.get("close", 0)) for x in bars]
        h = [float(x.get("high", 0)) for x in bars]
        l = [float(x.get("low", 0)) for x in bars]
        o = [float(x.get("open", 0)) for x in bars]
        v = [float(x.get("volume", 0)) for x in bars]
        i = len(c) - 1

        ma5 = sma(c, 5)
        vv5 = sma(v, 5)
        ma20 = sma(c, 20)

        if ma5[i] is None or vv5[i] is None or ma20[i] is None:
            return None

        # Price above MA5 and closed higher than open
        bull_bar = c[i] > float(ma5[i]) and c[i] > o[i]

        # Volume support
        vol_ok = v[i] >= 0.8 * float(vv5[i])

        # Prior context: was price recently below MA20 (indicating a prior decline)
        recent_low = min(l[-10:])
        declined_before = float(ma20[i]) > recent_low * 1.08 if recent_low else False

        # Consecutive gains: at least 2 of last 3 bars are green
        greens = sum(1 for j in range(max(0, i - 2), i + 1) if c[j] > o[j])

        if bull_bar and vol_ok and greens >= 2 and declined_before:
            return {
                "signal": "rebound_confirmation",
                "family": "rebound_reversal",
                "strength": "primary",
                "evidence": {
                    "above_ma5": True,
                    "volume_ratio": round(v[i] / float(vv5[i]), 4),
                    "consecutive_greens": greens,
                    "prior_decline_confirmed": declined_before,
                },
                "pattern_id": "rebound_confirmation",
                "pattern_version": "1.0.0",
            }
        return None

    def _volume_price_divergence(self, bars: list[dict[str, Any]], **context: Any) -> dict[str, Any]:
        """Detect bearish volume-price divergence.

        Criteria:
        - Price near 20-bar high
        - Volume declining over last 5 bars compared to 20-bar average
        - MACD showing possible divergence or weakening momentum
        """
        if len(bars) < 25:
            return None
        c = [float(x.get("close", 0)) for x in bars]
        h = [float(x.get("high", 0)) for x in bars]
        v = [float(x.get("volume", 0)) for x in bars]
        i = len(c) - 1

        high20 = max(h[-20:])
        near_high = c[i] >= 0.97 * high20 if high20 else False

        vv20 = sma(v, 20)
        if vv20[i] is None:
            return None
        v20_avg = float(vv20[i])
        v5_recent = sum(v[-5:]) / 5 if len(v) >= 5 else 1
        vol_decline = v5_recent < 0.8 * v20_avg if v20_avg else True

        # MACD weakening
        e12 = ema(c, 12)
        e26 = ema(c, 26)
        dif = [a - b for a, b in zip(e12, e26)]
        dea = ema(dif, 9)
        macd_weakening = False
        if len(dif) >= 3 and len(dea) >= 3:
            macd_weakening = dif[-1] < dea[-1] and dif[-2] >= dea[-2]  # bearish cross

        if near_high and vol_decline and macd_weakening:
            return {
                "signal": "volume_price_divergence",
                "family": "exit_defensive",
                "strength": "exit",
                "evidence": {
                    "near_20d_high": near_high,
                    "volume_decline_5d": vol_decline,
                    "volume_ratio_vs_20d": round(v5_recent / v20_avg, 4),
                    "macd_bearish_cross": macd_weakening,
                },
                "pattern_id": "volume_price_divergence",
                "pattern_version": "1.0.0",
            }
        return None

    def scan(self, bars: list[dict[str, Any]], *, market_regime: str = "unknown",
             sector_strength: str = "unknown") -> list[dict[str, Any]]:
        if len(bars) < 60: return []
        o=[_f(x.get("open")) for x in bars]; h=[_f(x.get("high")) for x in bars]
        l=[_f(x.get("low")) for x in bars]; c=[_f(x.get("close")) for x in bars]; v=[_f(x.get("volume")) for x in bars]
        ma5,ma10,ma20,ma60=sma(c,5),sma(c,10),sma(c,20),sma(c,60)
        vv5,vv10,vv20=sma(v,5),sma(v,10),sma(v,20)
        e12,e26=ema(c,12),ema(c,26); dif=[a-b for a,b in zip(e12,e26)]; dea=ema(dif,9)
        i=len(c)-1; hits: list[SignalHit]=[]

        # triple_golden_cross: all three crossings within latest five sessions.
        def recent_cross(a,b): return any(crossed_up(a,b,j) for j in range(max(1,i-4),i+1))
        price_cross=recent_cross(ma5,ma10); vol_cross=recent_cross(vv5,vv10)
        macd_cross=any(dif[j] > dea[j] and dif[j-1] <= dea[j-1] for j in range(max(1,i-4),i+1))
        low20=min(c[max(0,i-19):i+1]); rebound=(c[i]/low20-1) if low20 else 99
        up_vol=[v[j] for j in range(max(1,i-19),i+1) if c[j]>=c[j-1]]; dn_vol=[v[j] for j in range(max(1,i-19),i+1) if c[j]<c[j-1]]
        vol_improve=(mean(up_vol) > mean(dn_vol)) if up_vol and dn_vol else False
        if price_cross and vol_cross and macd_cross and rebound <= .25 and vol_improve:
            hits.append(SignalHit("triple_golden_cross","trend_breakout","primary",
                {"price_cross":True,"volume_cross":True,"macd_cross":True,"rebound_20d":rebound,"up_volume_gt_down":True},
                pattern_id="triple_golden_cross", pattern_version="1.0.0"))

        # ma_convergence_breakout
        if all(x is not None and x>0 for x in (ma5[i],ma10[i],ma20[i],vv20[i])):
            mas=[float(ma5[i]),float(ma10[i]),float(ma20[i])]; convergence=(max(mas)-min(mas))/mean(mas)
            body=(c[i]/o[i]-1) if o[i] else 0
            crossed_all = c[i] > max(mas) and c[i-1] <= max(float(ma5[i-1] or 0),float(ma10[i-1] or 0),float(ma20[i-1] or 0))
            if convergence <= .025 and body >= .03 and crossed_all and v[i] >= 1.5*float(vv20[i]):
                hits.append(SignalHit("ma_convergence_breakout","trend_breakout","primary",
                    {"convergence":convergence,"body_return":body,"volume_ratio":v[i]/float(vv20[i])},
                    pattern_id="ma_convergence_breakout", pattern_version="1.0.0"))

        # ma60_breakout_retest
        for b in range(max(60,i-15), i):
            if ma60[b] and vv20[b] and c[b] > float(ma60[b]) and c[b-1] <= float(ma60[b-1] or ma60[b]) and v[b] >= 1.5*float(vv20[b]):
                if 1 <= i-b <= 10 and ma60[i]:
                    dist=abs(c[i]/float(ma60[i])-1); vol_ratio=v[i]/v[b] if v[b] else 99
                    right_confirm=c[i]>float(ma60[i]) and c[i]>=o[i]
                    if dist<=.03 and vol_ratio<=.70 and right_confirm:
                        hits.append(SignalHit("ma60_breakout_retest","trend_pullback","primary",
                            {"breakout_days_ago":i-b,"distance_to_ma60":dist,"retest_volume_vs_breakout":vol_ratio},
                            pattern_id="ma60_breakout_retest", pattern_version="1.0.0"))
                        break

        # single_bull_hold
        for b in range(max(1,i-8),i):
                dayret=c[b]/c[b-1]-1 if c[b-1] else 0
                baseline = dayret>=.05
                if not baseline and b>=2:
                    baseline=(c[b]/c[b-2]-1>=.07 and c[b]>o[b] and c[b-1]>o[b-1])
                if baseline and all(l[j]>=l[b] for j in range(b+1,i+1)) and vv5[i] and v[i]>float(vv5[i]) and c[i]>max(h[b:i] or [h[b]]):
                    hits.append(SignalHit("single_bull_hold","trend_pullback","primary",
                        {"baseline_index":b,"baseline_low":l[b],"hold_days":i-b,"breakout":True},
                        pattern_id="single_bull_hold", pattern_version="1.0.0"))
                    break

        # long_bull_day7 confirmation only
        for b in range(max(20,i-8), max(20,i-5)+1):
            if vv20[b] and c[b-1] and c[b]/c[b-1]-1>=.05 and v[b]>=1.5*float(vv20[b]):
                days=i-b
                if 6<=days<=8 and min(l[b+1:i+1], default=l[b])>=l[b]:
                    rng=(max(h[b+1:i+1], default=h[b])-min(l[b+1:i+1], default=l[b]))/c[b]
                    if c[i]>max(h[b+1:i], default=h[b]) and rng<=.12:
                        hits.append(SignalHit("long_bull_day7","pattern_confirmation","confirmation",
                            {"days_since_long_bull":days,"consolidation_range":rng},
                            pattern_id="long_bull_day7", pattern_version="1.0.0"))
                        break

        # high_volume_breakout
        for b in range(max(20,i-10),i):
            if vv20[b] and v[b]>=1.5*float(vv20[b]) and c[i]>h[b]:
                hits.append(SignalHit("high_volume_breakout","trend_breakout","primary",
                    {"volume_day_index":b,"volume_ratio":v[b]/float(vv20[b]),"recorded_high":h[b],"breakout_close":c[i]},
                    pattern_id="high_volume_breakout", pattern_version="1.0.0")); break

        # low_volume_support_bull: support proxy = 20d low / MA20; confirmation only
        if vv20[i] and ma20[i]:
            low20p=min(l[max(0,i-19):i+1]); support=max(low20p,float(ma20[i])); dist=abs(c[i]/support-1) if support else 99
            body=(c[i]/o[i]-1) if o[i] else 0
            if v[i]<=.5*float(vv20[i]) and dist<=.02 and .003<=body<=.05:
                hits.append(SignalHit("low_volume_support_bull","trend_pullback","confirmation",
                    {"volume_ratio":v[i]/float(vv20[i]),"support":support,"distance":dist,"body_return":body},
                    pattern_id="low_volume_support_bull", pattern_version="1.0.0"))

        # MA5 momentum pullback
        if sector_strength != "weak" and i>=4:
            highs=True
            for j in range(i-3,i):
                if not ma5[j] or c[j]<=float(ma5[j]) or c[j] < max(c[max(0,j-19):j]): highs=False
            first_red = c[i-1] < o[i-1]
            dist=abs(c[i]/float(ma5[i])-1) if ma5[i] else 99
            if highs and first_red and dist<=.02:
                hits.append(SignalHit("ma5_momentum_pullback","trend_pullback","primary",
                    {"distance_to_ma5":dist,"first_pullback_red":True},
                    pattern_id="ma5_momentum_pullback", pattern_version="1.0.0"))

        # defensive exits
        body=abs(c[i]-o[i]); upper=h[i]-max(c[i],o[i]); rng=max(h[i]-l[i],1e-9)
        high_zone=c[i]>=max(c[max(0,i-19):i+1])*.97
        if high_zone and upper/rng>=.55 and body/rng<=.35:
            hits.append(SignalHit("shooting_star_high","exit_defensive","exit",
                {"upper_shadow_ratio":upper/rng,"high_zone":True},
                pattern_id="shooting_star_high", pattern_version="1.0.0"))
        if ma20[i] and c[i]<float(ma20[i]) and c[i-1]>=float(ma20[i-1] or ma20[i]):
            hits.append(SignalHit("ma20_break","exit_defensive","exit",
                {"close":c[i],"ma20":ma20[i]},
                pattern_id="ma20_break", pattern_version="1.0.0"))
        if ma5[i] and ma10[i] and crossed_up(ma10,ma5,i):
            hits.append(SignalHit("ma_bearish_cut","exit_defensive","exit",
                {"ma5":ma5[i],"ma10":ma10[i]},
                pattern_id="ma_bearish_cut", pattern_version="1.0.0"))
        # limit-up gene enrichment for primary trend_pullback signals
        for hit in hits:
            if hit.family == "trend_pullback" and hit.strength == "primary":
                hit.evidence["limitup_gene"] = self._limitup_gene(bars)
        return [x.to_dict() for x in hits]

    @staticmethod
    def build_registry() -> PatternRegistry:
        """Create and return a ``PatternRegistry`` populated with all 18 known patterns (14 daily + 4 intraday).  Each existing detection function is referenced by name so
        the registry stays a thin catalog wrapper — no logic duplication.
        """
        reg = PatternRegistry()

        # --- trend_breakout ---
        reg.register(PatternSpec(
            pattern_id="triple_golden_cross",
            pattern_version="1.0.0",
            family="trend_breakout",
            required_features=["ma5", "ma10", "ma20", "vv5", "vv10", "macd"],
            detect_func_name="_limitup_gene",  # enrichment func placeholder
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="ma_convergence_breakout",
            pattern_version="1.0.0",
            family="trend_breakout",
            required_features=["ma5", "ma10", "ma20", "vv20"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="high_volume_breakout",
            pattern_version="1.0.0",
            family="trend_breakout",
            required_features=["vv20"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        # --- trend_pullback ---
        reg.register(PatternSpec(
            pattern_id="ma60_breakout_retest",
            pattern_version="1.0.0",
            family="trend_pullback",
            required_features=["ma60", "vv20"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="single_bull_hold",
            pattern_version="1.0.0",
            family="trend_pullback",
            required_features=["vv5"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="ma5_momentum_pullback",
            pattern_version="1.0.0",
            family="trend_pullback",
            required_features=["ma5"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="low_volume_support_bull",
            pattern_version="1.0.0",
            family="trend_pullback",
            required_features=["vv20", "ma20"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        # --- rebound_reversal ---
        reg.register(PatternSpec(
            pattern_id="rebound_candidate",
            pattern_version="1.0.0",
            family="rebound_reversal",
            required_features=["close", "high", "low", "open", "volume"],
            detect_func_name="_rebound_candidate",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="rebound_confirmation",
            pattern_version="1.0.0",
            family="rebound_reversal",
            required_features=["ma5", "vv5", "ma20"],
            detect_func_name="_rebound_confirmation",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        # --- pattern_confirmation ---
        reg.register(PatternSpec(
            pattern_id="long_bull_day7",
            pattern_version="1.0.0",
            family="pattern_confirmation",
            required_features=["vv20"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        # --- exit_defensive ---
        reg.register(PatternSpec(
            pattern_id="shooting_star_high",
            pattern_version="1.0.0",
            family="exit_defensive",
            required_features=["close", "open", "high", "low"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="volume_price_divergence",
            pattern_version="1.0.0",
            family="exit_defensive",
            required_features=["close", "volume", "high"],
            detect_func_name="_volume_price_divergence",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="ma20_break",
            pattern_version="1.0.0",
            family="exit_defensive",
            required_features=["ma20"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="ma_bearish_cut",
            pattern_version="1.0.0",
            family="exit_defensive",
            required_features=["ma5", "ma10"],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        # === intraday momentum patterns (live MCP only) ===
        reg.register(PatternSpec(
            pattern_id="morning_surge",
            pattern_version="1.0.0",
            family="intraday_momentum",
            required_features=[],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="vwap_hold",
            pattern_version="1.0.0",
            family="intraday_momentum",
            required_features=[],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="afternoon_breakout",
            pattern_version="1.0.0",
            family="intraday_momentum",
            required_features=[],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        reg.register(PatternSpec(
            pattern_id="vwap_break_warning",
            pattern_version="1.0.0",
            family="exit_defensive",
            required_features=[],
            detect_func_name="",
            entry_rule={},
            invalidation_rule={},
            exit_rule={},
        ))
        return reg
