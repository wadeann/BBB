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
    trading_days_since_listing: int | None = None,
    trading_days_in_delisting: int | None = None,
    is_delisting_first_day: bool = False,
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

    # IPO no-price-limit period
    if trading_days_since_listing is not None:
        if ipo_no_price_limit(symbol, as_of or "", trading_days_since_listing=trading_days_since_listing, board=b):
            return 999.0  # Effectively unlimited

    # Delisting transition period first day (退市整理期首日无价格涨跌幅限制)
    if is_delisting_first_day or delisting_no_price_limit(symbol, status=status, trading_days_in_delisting=trading_days_in_delisting, board=b):
        return 999.0

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
    trading_days_since_listing: int | None = None,
    trading_days_in_delisting: int | None = None,
    is_delisting_first_day: bool = False,
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
        trading_days_since_listing=trading_days_since_listing,
        trading_days_in_delisting=trading_days_in_delisting,
        is_delisting_first_day=is_delisting_first_day,
    )
    if lim >= 100.0:
        return False  # No price limit, cannot be locked at limit
    o = float(bar.get("open", 0))
    h = float(bar.get("high", 0))
    l = float(bar.get("low", 0))
    if direction.upper() == "BUY":
        at = o / prev_close - 1 >= lim - eps
    else:
        at = o / prev_close - 1 <= -lim + eps
    one_price = abs(h - l) <= max(1e-8, o * 1e-5)
    return bool(at and one_price)


def board_aware_lot_size(
    symbol: str,
    quantity: int,
    direction: str = "BUY",
    board: str | None = None,
) -> int:
    """Normalize order quantity to board-specific lot/increment rules.

    Rules:
    - SSE/SZSE Main Board: BUY minimum 100 shares, order must be in multiples of 100 shares.
    - STAR (科创板): BUY minimum 200 shares; above 200 can increment by 1 share.
    - BSE (北交所): BUY minimum 100 shares; above 100 can increment by 1 share.
    - ChiNext (创业板): BUY minimum 100 shares, order must be in multiples of 100 shares.
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
            b = "SSE_MAIN"

    qty = int(quantity)
    if direction.upper() == "BUY":
        if b == "STAR":
            # STAR: minimum 200 shares, then 1-share increments
            return qty if qty >= 200 else 0
        elif b == "BSE":
            # BSE: minimum 100 shares, then 1-share increments
            return qty if qty >= 100 else 0
        else:
            # SSE Main, SZSE Main, ChiNext: 100-share board lot (multiples of 100)
            lot = 100
            rounded = (qty // lot) * lot
            return rounded if rounded >= lot else 0
    else:
        # SELL: can sell any odd lots held
        return max(0, qty)


def ipo_no_price_limit(
    symbol: str,
    trade_date: str,
    listing_date: str | None = None,
    trading_days_since_listing: int | None = None,
    board: str | None = None,
) -> bool:
    """Determine if a stock is in its IPO no-price-limit period.

    Rules:
    - SSE Main / SZSE Main: first 5 trading days after listing -> no price limit.
    - STAR (科创板): first 5 trading days -> no price limit.
    - ChiNext (创业板): first 5 trading days -> no price limit.
    - BSE (北交所): listing day only (1 trading day) -> no price limit.
    """
    if trading_days_since_listing is None or trading_days_since_listing <= 0:
        return False
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
        else:
            b = "SZSE_MAIN"

    if b == "BSE":
        return trading_days_since_listing <= 1
    # SSE_MAIN, SZSE_MAIN, STAR, CHINEXT: first 5 trading days
    return trading_days_since_listing <= 5


def delisting_no_price_limit(
    symbol: str,
    status: str | None = None,
    trading_days_in_delisting: int | None = None,
    board: str | None = None,
) -> bool:
    """Determine if a stock is on the first day of its delisting transition period (退市整理期首日).
    Applicable markets: SSE (Main, STAR) and SZSE (Main, ChiNext).
    On the first day of delisting transition, there is NO price limit.
    """
    st = str(status or "").upper()
    if st != "DELISTING":
        return False
    if trading_days_in_delisting is not None and trading_days_in_delisting == 1:
        return True
    return False
