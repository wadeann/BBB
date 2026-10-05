from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any


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
