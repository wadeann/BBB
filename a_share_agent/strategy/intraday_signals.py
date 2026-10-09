"""
分钟级别盘面模式检测

在盘中运行时被 MCP 调用（fetch_kline period="15" 或 "5"），不在回测中运行。
回测只有日线数据，所以这些模式无法回测——它们是纯盘中信号。
"""

from __future__ import annotations

from typing import Any
from .signal_engine import SignalHit, _f, sma


def _vwap(bars: list[dict[str, Any]]) -> list[float | None]:
    """Volume-weighted average price per bar."""
    out: list[float | None] = [None] * len(bars)
    cum_pv = 0.0
    cum_vol = 0.0
    for i, b in enumerate(bars):
        typ_price = (float(b.get("high", 0)) + float(b.get("low", 0)) + float(b.get("close", 0))) / 3
        vol = float(b.get("volume", 0))
        cum_pv += typ_price * vol
        cum_vol += vol
        if cum_vol > 0:
            out[i] = cum_pv / cum_vol
    return out


def intraday_scan(
    minute_bars: list[dict[str, Any]],
    *,
    period_minutes: int = 15,
    daily_open: float | None = None,
    prev_close: float | None = None,
) -> list[dict[str, Any]]:
    """
    盘中分钟级模式检测入口。

    Parameters
    ----------
    minute_bars : list[dict]
        分钟级 K 线列表（每根含 open/high/low/close/volume），
        时间升序。来自 Sina/Tencent fetch_kline。
    period_minutes : int
        每根 K 线的分钟数（5/15/30/60）。影响指标窗口计算。
    daily_open : float, optional
        今日开盘价。若未传入则用第一根 minute_bar 的 open。
    prev_close : float, optional
        昨日收盘价。用于计算涨跌幅。若未传入则用 minute_bars[-1].close - change。

    Returns
    -------
    list[dict]
        命中模式列表（与 DeterministicSignalEngine.scan 格式兼容）。
    """
    if len(minute_bars) < 13:
        return []  # 至少需要 13 根才能计算有意义指标

    hits: list[SignalHit] = []

    c = [_f(x.get("close")) for x in minute_bars]
    h = [_f(x.get("high")) for x in minute_bars]
    l = [_f(x.get("low")) for x in minute_bars]
    o = [_f(x.get("open")) for x in minute_bars]
    v = [_f(x.get("volume")) for x in minute_bars]

    i = len(c) - 1
    daily_open = daily_open or o[0]
    period_per_day = int(240 / period_minutes)  # A股每日240分钟

    # --- 1. 早盘冲高 (morning_surge) ---
    # 开盘后前 period_per_day/4 根 K 线内(约上午10点前)，
    # 价格从开盘拉升 >= 2%，且成交量是同期均量的 1.5 倍以上
    morning_end = max(period_per_day // 4, 5)
    if i <= morning_end and i >= 3:
        surge = (c[i] / daily_open - 1) * 100
        v_avg = sum(v[1:i+1]) / max(i, 1) if i > 0 else v[i]
        vol_spike = v[i] >= 1.5 * v_avg if v_avg else False
        if surge >= 2.0 and vol_spike:
            hits.append(SignalHit(
                "morning_surge", "intraday_momentum", "primary",
                {"surge_pct": round(surge, 2), "volume_ratio": round(v[i] / v_avg, 2) if v_avg else 0},
                pattern_id="morning_surge", pattern_version="1.0.0",
            ))

    # --- 2. VWAP 站稳 (vwap_hold) ---
    # 盘中大部分时间价格在 VWAP 上方运行，且最新价仍在 VWAP 上方
    vwap = _vwap(minute_bars)
    if vwap[i] and vwap[i] > 0:
        bars_above = sum(1 for j in range(max(0, i - period_per_day // 2), i + 1)
                         if vwap[j] and c[j] > vwap[j])
        total_check = min(i + 1, period_per_day // 2)
        if total_check >= 6 and bars_above / total_check >= 0.6 and c[i] > vwap[i]:
            hits.append(SignalHit(
                "vwap_hold", "intraday_momentum", "confirmation",
                {"bars_above_vwap_ratio": round(bars_above / total_check, 2),
                 "distance_to_vwap_pct": round((c[i] / vwap[i] - 1) * 100, 2)},
                pattern_id="vwap_hold", pattern_version="1.0.0",
            ))

    # --- 3. 尾盘加速 (afternoon_breakout) ---
    # 下午(period_per_day 后半段)价格突破上午高点
    if len(minute_bars) >= period_per_day:  # 有足够全天数据
        midday = max(len(minute_bars) // 2, period_per_day // 2)
        am_high = max(h[:midday]) if midday > 0 else h[0]
        pm_bars = minute_bars[midday:]
        if len(pm_bars) >= 4:
            pm_close = [_f(x.get("close")) for x in pm_bars]
            pm_volume = [_f(x.get("volume")) for x in pm_bars]
            if pm_close[-1] > am_high * 1.005:  # 突破上午高点 0.5%
                v_ratio = sum(pm_volume[-4:]) / (sum(pm_volume) / len(pm_volume)) if sum(pm_volume) else 1
                if v_ratio >= 1.2:
                    hits.append(SignalHit(
                        "afternoon_breakout", "intraday_momentum", "primary",
                        {"am_high": am_high, "breakout_price": pm_close[-1],
                         "afternoon_vol_ratio": round(v_ratio, 2)},
                        pattern_id="afternoon_breakout", pattern_version="1.0.0",
                    ))

    # --- 4. VWAP 跌破预警 (vwap_break_warning) ---
    # 价格从 VWAP 上方跌到下方，且成交量放大 -> 盘中走弱预警
    if vwap[i] and vwap[i] > 0 and len(minute_bars) >= 6:
        prev_above = sum(1 for j in range(max(0, i - 5), i) if vwap[j] and c[j] > vwap[j])
        if prev_above >= 4 and c[i] < vwap[i] and v[i] >= sma(v, 5)[i] * 1.3 if sma(v, 5)[i] else False:
            hits.append(SignalHit(
                "vwap_break_warning", "exit_defensive", "exit",
                {"bars_above_before_break": prev_above, "vwap": vwap[i], "close": c[i]},
                pattern_id="vwap_break_warning", pattern_version="1.0.0",
            ))

    return [x.to_dict() for x in hits]
