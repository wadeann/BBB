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


def price_limit_pct(symbol: str, status: str | None = None, is_st: bool = False) -> float:
    if is_st or (status and str(status).upper() in {"ST", "*ST"}):
        return 0.05
    s = symbol.split(".")[0]
    upper = symbol.upper()
    if upper.endswith(".BJ") or s.startswith(("8", "4", "92")):
        return 0.30
    if s.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10


def locked_at_limit(bar: dict, prev_close: float, direction: str, eps: float = 0.003, status: str | None = None, is_st: bool = False) -> bool:
    if not prev_close or prev_close <= 0:
        return False
    lim = price_limit_pct(str(bar.get("symbol", "")), status=status, is_st=is_st)
    o = float(bar.get("open", 0))
    h = float(bar.get("high", 0))
    l = float(bar.get("low", 0))
    if direction.upper() == "BUY":
        at = o / prev_close - 1 >= lim - eps
    else:
        at = o / prev_close - 1 <= -lim + eps
    one_price = abs(h - l) <= max(1e-8, o * 1e-5)
    return bool(at and one_price)
