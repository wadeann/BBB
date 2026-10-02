from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AShareCostModel:
    commission_rate: float = 0.0003
    commission_min: float = 5.0
    stamp_tax_rate_sell: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0

    def slip_price(self, raw_price: float, direction: str) -> float:
        factor = self.slippage_bps / 10000.0
        return raw_price * (1 + factor if direction.upper()=="BUY" else 1-factor)

    def fees(self, amount: float, direction: str) -> float:
        commission=max(self.commission_min, amount*self.commission_rate)
        stamp=amount*self.stamp_tax_rate_sell if direction.upper()=="SELL" else 0.0
        transfer=amount*self.transfer_fee_rate
        return commission+stamp+transfer


def price_limit_pct(
    symbol: str,
    as_of: str | None = None,
    *,
    status: str | None = None,
    is_st: bool = False,
    exchange: str | None = None,
    board: str | None = None,
) -> float:
    """Dynamically determine historical price limit percentage based on:
    date + exchange + board + risk_warning.

    Rules:
    - BSE (北交所): 30% (0.30)
    - STAR (科创板): 20% (0.20) regardless of ST status
    - ChiNext (创业板): 20% (0.20) regardless of ST status
    - SSE_MAIN / SZSE_MAIN (沪深主板):
      - Normal: 10% (0.10)
      - ST / *ST (risk_warning):
        - Before 2026-07-06: 5% (0.05)
        - On or after 2026-07-06: 10% (0.10) (2026-07-06 rule switch)
    """
    s = symbol.split(".")[0]
    upper = symbol.upper()

    b = (board or "").upper()
    if not b:
        if upper.endswith(".BJ") or s.startswith(("4", "8", "92")):
            b = "BSE"
        elif upper.endswith(".SH") and s.startswith(("688", "689")):
            b = "STAR"
        elif upper.endswith(".SZ") and s.startswith(("300", "301")):
            b = "CHINEXT"
        elif upper.endswith(".SH") or s.startswith(("600", "601", "603", "605")):
            b = "SSE_MAIN"
        elif upper.endswith(".SZ") or s.startswith(("000", "001", "002", "003")):
            b = "SZSE_MAIN"
        else:
            b = "SSE_MAIN" if upper.endswith(".SH") else "SZSE_MAIN"

    has_st = bool(is_st or (status and str(status).upper() in {"ST", "*ST"}))

    if b == "BSE":
        return 0.30
    if b in {"STAR", "CHINEXT"}:
        return 0.20

    # Main Boards (SSE_MAIN, SZSE_MAIN)
    if has_st:
        # Rule switchover on 2026-07-06:
        # Before 2026-07-06: ST was 5%
        # Starting 2026-07-06: ST switched to 10%
        if as_of and str(as_of) >= "2026-07-06":
            return 0.10
        return 0.05

    return 0.10


def locked_at_limit(
    bar: dict,
    prev_close: float,
    direction: str,
    eps: float = 0.003,
    status: str | None = None,
    is_st: bool = False,
    as_of: str | None = None,
    board: str | None = None,
    exchange: str | None = None,
) -> bool:
    if not prev_close or prev_close <= 0:
        return False
    trade_date = as_of or str(bar.get("date") or bar.get("time") or "")
    lim = price_limit_pct(
        str(bar.get("symbol", "")),
        trade_date,
        status=status,
        is_st=is_st,
        board=board,
        exchange=exchange,
    )
    o = float(bar.get("open", 0))
    h = float(bar.get("high", 0))
    l = float(bar.get("low", 0))
    if direction.upper() == "BUY":
        at = o / prev_close - 1 >= lim - eps
    else:
        at = o / prev_close - 1 <= -lim + eps
    one_price = abs(h - l) <= max(1e-8, o * 1e-5)
    return bool(at and one_price)
