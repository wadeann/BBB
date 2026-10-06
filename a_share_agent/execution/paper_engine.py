"""Phase 6 Paper Trading Engine — Signal→Risk→Intent→Order→Fill→Position→Exit.

SELF-CONTAINED: No real broker calls, no real MCP exec endpoints,
no production side effects. F07 contract: allow_real_execution always False.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Callable

from ..risk.paper_risk import PaperRiskEngine, PaperRiskResult
from ..utils import now_shanghai, parse_iso, as_trade_date
from .paper_ledger import PaperLedger, PaperOrder, PaperPosition


# ---------------------------------------------------------------------------
# Shared SignalDecision type mirror — kept local for P5 independence
# ---------------------------------------------------------------------------


@dataclass
class SignalDecision:
    decision_id: str
    symbol: str
    pattern_id: str
    pattern_version: str
    status: str  # WATCH | BUY_READY | BUY_TRIGGERED | HOLD | SELL_WARNING | SELL_TRIGGERED | INVALIDATED
    context: dict | None = None
    candidate: dict | None = None  # Candidate
    entry_plan: dict | None = None  # {limit_price, max_quantity, stop_price, timestamp}
    exit_plan: dict | None = None  # {exit_reason, exit_price, timestamp}
    alerts: list[dict] | None = None
    created_at: str | None = None
    state_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> SignalDecision:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Fill simulation result
# ---------------------------------------------------------------------------


@dataclass
class FillResult:
    filled: bool
    fill_price: float
    fill_quantity: int
    fill_pct: float  # 0.0 – 1.0 of requested
    slippage_bps: int
    is_partial: bool = False


# ---------------------------------------------------------------------------
# Paper Engine
# ---------------------------------------------------------------------------


class PaperEngine:
    """Full paper execution lifecycle.

    Design:
    - Accepts SignalDecision (BUY_TRIGGERED / SELL_TRIGGERED)
    - Runs fail-closed risk checks via PaperRiskEngine
    - Creates immutable Intent → PaperOrder
    - Simulates fills via injected price callback or manual tick
    - Tracks positions, P&L, MAE/MFE via PaperLedger
    - T+1 enforcement, board lot rounding, gross/net retention
    """

    def __init__(
        self,
        ledger: PaperLedger,
        risk: PaperRiskEngine,
        *,
        initial_cash: float = 1_000_000.0,
        allow_real_execution: bool = False,
        price_provider: Callable[[str, str | None], dict[str, Any]] | None = None,
        trading_calendar: Callable[[str], list[str]] | None = None,
        sector_map: dict[str, str] | None = None,
        board_func: Callable[[str, str], str] | None = None,
    ):
        self._ledger = ledger
        self._risk = risk
        self._initial_cash = initial_cash
        self._allow_real_execution = allow_real_execution
        self._price_provider = price_provider  # fn(symbol, trade_date) -> {open, high, low, close, prev_close}
        self._trading_calendar = trading_calendar  # fn(trade_date) -> [next_trade_dates...]
        self._sector_map = sector_map or {}
        self._board_func = board_func or _default_board
        self._trade_date: str | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def ledger(self) -> PaperLedger:
        return self._ledger

    @property
    def cash(self) -> float:
        return self._ledger.cash_balance(self._initial_cash)

    @property
    def equity(self) -> float:
        return self.cash + self._ledger.total_market_value()

    # ------------------------------------------------------------------
    # Main processing: signal → order chain
    # ------------------------------------------------------------------

    def process_signal(
        self,
        decision: SignalDecision | dict[str, Any],
        *,
        trade_date: str | None = None,
        quote: dict[str, Any] | None = None,
        positions: list[dict[str, Any]] | None = None,
        open_orders: list[dict[str, Any]] | None = None,
        blacklist: Any = None,
    ) -> dict[str, Any]:
        """Process a SignalDecision through the full paper lifecycle.

        Returns a result dict with keys:
          - decision_id: str
          - status: CREATED | REJECTED | ERROR
          - order: PaperOrder.to_dict() | None
          - risk: PaperRiskResult.to_dict() | None
          - error: str | None
          - reject_reason: str | None
        """
        # Normalize input
        if isinstance(decision, dict):
            try:
                decision = SignalDecision.from_dict(decision)
            except Exception as exc:
                return {"decision_id": "", "status": "ERROR", "error": f"invalid decision dict: {exc}"}

        td = trade_date or as_trade_date()
        self._trade_date = td

        # Only process triggered decisions
        if decision.status not in ("BUY_TRIGGERED", "SELL_TRIGGERED"):
            return {
                "decision_id": decision.decision_id,
                "status": "REJECTED",
                "reject_reason": f"NOT_TRIGGERED: {decision.status}",
            }

        direction = "BUY" if decision.status == "BUY_TRIGGERED" else "SELL"
        entry_plan = decision.entry_plan or {}
        exit_plan = decision.exit_plan or {}

        if direction == "BUY":
            limit_price = float(entry_plan.get("limit_price", 0))
            max_quantity = int(entry_plan.get("max_quantity", 0))
            stop_price = float(entry_plan.get("stop_price")) if entry_plan.get("stop_price") is not None else None
            if limit_price <= 0 or max_quantity <= 0:
                return {
                    "decision_id": decision.decision_id,
                    "status": "REJECTED",
                    "reject_reason": "INVALID_ENTRY_PLAN: missing limit_price or max_quantity",
                }
        else:
            limit_price = float(exit_plan.get("exit_price", 0))
            # For sell, use position quantity
            pos = self._ledger.get_position(decision.symbol)
            max_quantity = pos.quantity if pos else 0
            stop_price = None
            if limit_price <= 0 or max_quantity <= 0:
                return {
                    "decision_id": decision.decision_id,
                    "status": "REJECTED",
                    "reject_reason": "INVALID_EXIT_PLAN: missing exit_price or position",
                }

        fetched_positions = positions if positions is not None else [p.to_dict() for p in self._ledger.list_positions(status="OPEN")]
        fetched_open_orders = open_orders if open_orders is not None else [o.to_dict() for o in self._ledger.list_orders(status="CREATED") + self._ledger.list_orders(status="SUBMITTED") + self._ledger.list_orders(status="PARTIAL_FILLED")]

        # Get current price for risk checks
        if quote:
            current_price = float(quote.get("price", quote.get("open", quote.get("close", limit_price))))
        else:
            current_price = limit_price

        # Determine limit up/down
        limit_up_pct = None
        limit_down_pct = None
        if quote:
            prev_close = float(quote.get("prev_close", 0))
            if prev_close > 0:
                limit_up_pct = (current_price - prev_close) / prev_close
                limit_down_pct = (current_price - prev_close) / prev_close

        # Run risk checks
        risk_result = self._risk.assess(
            direction=direction,
            symbol=decision.symbol,
            requested_quantity=max_quantity,
            entry_price=current_price,
            stop_price=stop_price,
            trade_date=td,
            cash=self._ledger.cash_balance(self._initial_cash),
            positions=fetched_positions,
            open_orders=fetched_open_orders,
            blacklist=blacklist,
            sector=decision.candidate.get("sector") if decision.candidate else None,
            sector_map=self._sector_map,
            bought_today=self._ledger.bought_today,
            total_equity=self.equity,
            limit_up_pct=limit_up_pct,
            limit_down_pct=limit_down_pct,
            is_suspended=bool(quote.get("suspended")) if quote else False,
        )

        if risk_result.status != "APPROVED":
            return {
                "decision_id": decision.decision_id,
                "status": "REJECTED",
                "reject_reason": risk_result.reason,
                "risk": risk_result.to_dict(),
            }

        # Apply board lot rounding
        board = self._board_func(decision.symbol, td) if self._board_func else ""
        approved_qty = risk_result.approved_quantity
        rounded_qty = _board_round_lot(decision.symbol, approved_qty, direction, board)

        if rounded_qty <= 0:
            return {
                "decision_id": decision.decision_id,
                "status": "REJECTED",
                "reject_reason": "BOARD_LOT_ROUNDING_ZERO",
                "risk": risk_result.to_dict(),
            }

        # Create order
        now_iso = now_shanghai().isoformat()
        order = PaperOrder(
            order_id=f"ORD-{uuid.uuid4().hex[:12]}",
            decision_id=decision.decision_id,
            symbol=decision.symbol,
            direction=direction,
            intent_id=f"INT-{td.replace('-','')}-{uuid.uuid4().hex[:12]}",
            quantity=rounded_qty,
            limit_price=current_price,
            status="CREATED",
            created_at=now_iso,
        )
        self._ledger.register_order(order)

        return {
            "decision_id": decision.decision_id,
            "status": "CREATED",
            "order": order.to_dict(),
            "risk": risk_result.to_dict(),
        }

    # ------------------------------------------------------------------
    # Fill simulation
    # ------------------------------------------------------------------

    def fill_order(
        self,
        order_id: str,
        *,
        market_price: float | None = None,
        slippage_bps: int | None = None,
        fill_pct: float = 1.0,
        trade_date: str | None = None,
    ) -> dict[str, Any]:
        """Simulate filling an order.

        Args:
            order_id: The order to fill.
            market_price: Price to fill at. If None, uses price_provider.
            slippage_bps: Slippage in bps. Defaults to risk engine's setting.
            fill_pct: Fraction of remaining quantity to fill (0.0-1.0).
            trade_date: Current trade date for position tracking.

        Returns:
            dict with fill result.
        """
        order = self._ledger.get_order(order_id)
        if not order:
            return {"order_id": order_id, "error": "ORDER_NOT_FOUND", "filled": False}

        td = trade_date or self._trade_date or as_trade_date()

        # Determine fill price
        if market_price is not None:
            raw_price = market_price
        elif self._price_provider:
            bar = self._price_provider(order.symbol, td)
            raw_price = float(bar.get("open", bar.get("close", bar.get("price", 0))))
        else:
            raw_price = order.limit_price

        # Apply slippage
        slippage = slippage_bps if slippage_bps is not None else self._risk._slippage_bps
        fill_price = self._risk.slip_price(raw_price, order.direction)

        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            return {"order_id": order_id, "error": "ALREADY_FILLED", "filled": False}

        fill_qty = max(1, int(remaining * fill_pct))

        # Board lot rounding for fill
        board = self._board_func(order.symbol, td) if self._board_func else ""
        fill_qty = _board_round_lot(order.symbol, fill_qty, order.direction, board)
        if fill_qty <= 0:
            fill_qty = remaining  # fallback to at least 1 share

        # Safety: don't exceed remaining
        fill_qty = min(fill_qty, remaining)

        is_partial = fill_pct < 1.0 or fill_qty < remaining
        new_filled_qty = order.filled_quantity + fill_qty
        now_iso = now_shanghai().isoformat()

        if is_partial:
            order.status = "PARTIAL_FILLED"
            order.filled_quantity = new_filled_qty
            order.filled_price = fill_price
            if order.filled_at is None:
                order.filled_at = now_iso
        else:
            order.status = "FILLED"
            order.filled_quantity = new_filled_qty
            order.filled_price = fill_price
            order.filled_at = now_iso

        self._ledger.update_order(
            order_id,
            status=order.status,
            filled_quantity=order.filled_quantity,
            filled_price=order.filled_price,
            filled_at=order.filled_at,
        )

        # Open or update position
        if order.direction == "BUY":
            existing = self._ledger.get_position(order.symbol)
            if existing and existing.status == "OPEN":
                # Add to existing position (average cost)
                total_qty = existing.quantity + fill_qty
                avg_price = ((existing.entry_price * existing.quantity) + (fill_price * fill_qty)) / total_qty if total_qty > 0 else fill_price
                existing.quantity = total_qty
                existing.entry_price = avg_price
                existing.gross_entry_cost = avg_price * total_qty
                existing.current_price = fill_price
                existing.fees_paid += self._estimate_fees(fill_price * fill_qty, "BUY")
            else:
                pos = self._ledger.open_position(
                    symbol=order.symbol,
                    direction="LONG",
                    quantity=fill_qty,
                    entry_price=fill_price,
                    current_price=fill_price,
                    raw_entry_price=raw_price,
                    fees=self._estimate_fees(fill_price * fill_qty, "BUY"),
                    trade_date=td,
                )
        else:  # SELL
            # Close or reduce position
            existing = self._ledger.get_position(order.symbol)
            if existing and existing.status == "OPEN":
                if fill_qty >= existing.quantity:
                    self._ledger.close_position(
                        symbol=order.symbol,
                        exit_price=fill_price,
                        exit_reason="SIGNAL_EXIT",
                        fees=self._estimate_fees(fill_price * fill_qty, "SELL"),
                        raw_exit_price=raw_price,
                    )
                else:
                    self._ledger.partial_close(
                        symbol=order.symbol,
                        exit_price=fill_price,
                        exit_quantity=fill_qty,
                        exit_reason="SIGNAL_EXIT",
                        fees=self._estimate_fees(fill_price * fill_qty, "SELL"),
                        raw_exit_price=raw_price,
                    )

        return {
            "order_id": order_id,
            "filled": True,
            "fill_price": fill_price,
            "raw_price": raw_price,
            "fill_quantity": fill_qty,
            "slippage_bps": slippage,
            "is_partial": is_partial,
            "order_status": order.status,
        }

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order and release reserved cash/position."""
        order = self._ledger.get_order(order_id)
        if not order:
            return {"order_id": order_id, "error": "ORDER_NOT_FOUND"}
        if order.status in ("FILLED", "CANCELLED", "REJECTED"):
            return {"order_id": order_id, "error": f"CANNOT_CANCEL: {order.status}"}

        order.status = "CANCELLED"
        self._ledger.update_order(order_id, status="CANCELLED")
        return {"order_id": order_id, "status": "CANCELLED"}

    def reject_order(self, order_id: str, reason: str = "ADMIN_REJECT") -> dict[str, Any]:
        """Reject an order for failure/reason."""
        order = self._ledger.get_order(order_id)
        if not order:
            return {"order_id": order_id, "error": "ORDER_NOT_FOUND"}

        order.status = "REJECTED"
        order.reject_reason = reason
        self._ledger.update_order(order_id, status="REJECTED", reject_reason=reason)
        return {"order_id": order_id, "status": "REJECTED", "reason": reason}

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def update_prices(self, prices: dict[str, float], trade_date: str | None = None) -> None:
        """Update all positions with current prices (for daily P&L)."""
        td = trade_date or self._trade_date or as_trade_date()
        for pos in self._ledger.list_positions(status="OPEN"):
            if pos.symbol in prices:
                self._ledger.update_price(pos.symbol, prices[pos.symbol], td)

    # ------------------------------------------------------------------
    # New trading day
    # ------------------------------------------------------------------

    def new_trade_date(self, trade_date: str, prev_trade_date: str | None = None) -> None:
        """Advance to new trading day (T+1 release, holding days)."""
        self._trade_date = trade_date
        self._ledger.new_trade_date(trade_date, prev_trade_date)

    # ------------------------------------------------------------------
    # Fee estimation (mirror of risk engine)
    # ------------------------------------------------------------------

    def _estimate_fees(self, amount: float, direction: str) -> float:
        return self._risk._estimate_fees(amount, direction)

    # ------------------------------------------------------------------
    # Market value
    # ------------------------------------------------------------------

    @property
    def total_market_value(self) -> float:
        return self._ledger.total_market_value()

    @property
    def total_pnl(self) -> float:
        return self._ledger.total_pnl()


# ---------------------------------------------------------------------------
# Board lot rounding
# ---------------------------------------------------------------------------


def _board_round_lot(symbol: str, quantity: int, direction: str, board: str = "") -> int:
    """Round quantity to valid board lot size.

    Board detection by suffix:
      - 688xxx / .SH + STAR = 200 min, 1-increment
      - 920xxx / .BJ = 100 min, 1-increment  
      - Other = 100 board lot
    """
    if quantity <= 0:
        return 0

    s = symbol.split(".")[0]
    sym_upper = symbol.upper()

    # Detect board from symbol prefix
    if s.startswith("688") or board == "STAR":
        # STAR: min 200 shares, then 1-share increments
        return quantity if quantity >= 200 else 0
    elif s.startswith("920") or board == "BSE" or "BJ" in sym_upper.split(".")[-1:]:
        # BSE: min 100 shares, then 1-share increments
        return quantity if quantity >= 100 else 0
    else:
        # SSE/SZSE/ChiNext: 100 board lot (multiples of 100)
        lot = 100
        rounded = (quantity // lot) * lot
        return rounded if rounded >= lot else 0


def _default_board(symbol: str, trade_date: str = "") -> str:
    """Detect board from symbol prefix."""
    s = symbol.split(".")[0]
    if s.startswith("688"):
        return "STAR"
    if "BJ" in symbol.upper().split(".")[-1:]:
        return "BSE"
    if s.startswith("920"):
        return "BSE"
    if s.startswith("300"):
        return "CHINEXT"
    return "MAIN"
