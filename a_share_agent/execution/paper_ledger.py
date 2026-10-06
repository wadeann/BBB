"""Phase 6 Paper Trading Ledger — position tracking + P&L + persistence."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..utils import now_shanghai, parse_iso


# ---------------------------------------------------------------------------
# Dataclasses (contract-aligned)
# ---------------------------------------------------------------------------


@dataclass
class PaperOrder:
    order_id: str
    decision_id: str
    symbol: str
    direction: str  # BUY | SELL
    intent_id: str
    quantity: int
    limit_price: float
    status: str  # CREATED | SUBMITTED | PARTIAL_FILLED | FILLED | REJECTED | CANCELLED
    filled_quantity: int = 0
    filled_price: float | None = None
    reject_reason: str | None = None
    created_at: str | None = None
    filled_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None or k in (
            "order_id", "decision_id", "symbol", "direction", "intent_id", "quantity", "limit_price", "status",
            "filled_quantity",
        )}


@dataclass
class PaperPosition:
    position_id: str
    symbol: str
    direction: str  # LONG
    quantity: int
    entry_price: float
    current_price: float
    stop_price: float | None = None
    entry_at: str | None = None
    holding_days: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    pattern_id: str = ""
    pattern_version: str = ""
    regime_at_entry: str = ""
    lifecycle_at_entry: str = ""
    status: str = "OPEN"  # OPEN | CLOSED
    exit_reason: str | None = None
    exit_at: str | None = None
    round_trip_id: str | None = None
    # Internal fields for cost tracking
    gross_entry_cost: float = 0.0  # entry_price * quantity (before fees)
    gross_exit_value: float = 0.0  # exit_price * quantity (before fees)
    fees_paid: float = 0.0
    raw_entry_price: float | None = None  # price before slippage
    raw_exit_price: float | None = None  # price before slippage

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None or k in (
            "position_id", "symbol", "direction", "quantity", "entry_price", "current_price", "status",
        )}


@dataclass
class RoundTrip:
    round_trip_id: str
    symbol: str
    pattern_id: str
    pattern_version: str
    entry_timestamp: str
    exit_timestamp: str | None = None
    entry_price: float = 0.0
    exit_price: float | None = None
    quantity: int = 0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    holding_days: int = 0
    exit_reason: str | None = None
    regime_at_entry: str = ""
    lifecycle_at_entry: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# ---------------------------------------------------------------------------
# PaperLedger
# ---------------------------------------------------------------------------


class PaperLedger:
    """Position tracking, P&L, persistence, and restart recovery."""

    def __init__(self, persist_path: str | Path | None = None):
        self._positions: dict[str, PaperPosition] = {}  # symbol -> position
        self._round_trips: list[RoundTrip] = []
        self._orders: dict[str, PaperOrder] = {}
        self._bought_today: set[str] = set()  # symbols bought on current trade date
        self._known_order_ids: set[str] = set()
        self._persist_path = Path(persist_path) if persist_path else None
        self._last_trade_date: str | None = None

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------

    def open_position(
        self,
        *,
        symbol: str,
        direction: str,
        quantity: int,
        entry_price: float,
        current_price: float | None = None,
        stop_price: float | None = None,
        pattern_id: str = "",
        pattern_version: str = "",
        regime_at_entry: str = "",
        lifecycle_at_entry: str = "",
        fees: float = 0.0,
        raw_entry_price: float | None = None,
        entry_at: str | None = None,
        trade_date: str | None = None,
    ) -> PaperPosition:
        position_id = f"POS-{uuid.uuid4().hex[:12]}"
        now_iso = entry_at or now_shanghai().isoformat()
        pos = PaperPosition(
            position_id=position_id,
            symbol=symbol,
            direction=direction.upper(),
            quantity=quantity,
            entry_price=entry_price,
            current_price=current_price or entry_price,
            stop_price=stop_price,
            entry_at=now_iso,
            holding_days=1,
            pnl=0.0,
            pnl_pct=0.0,
            mfe=0.0,
            mae=0.0,
            pattern_id=pattern_id,
            pattern_version=pattern_version,
            regime_at_entry=regime_at_entry,
            lifecycle_at_entry=lifecycle_at_entry,
            status="OPEN",
            gross_entry_cost=entry_price * quantity,
            fees_paid=fees,
            raw_entry_price=raw_entry_price or entry_price,
        )
        self._positions[symbol] = pos

        # Track bought today for T+1 enforcement
        if trade_date:
            self._bought_today.add(symbol)
            self._last_trade_date = trade_date

        self._save()
        return pos

    def close_position(
        self,
        *,
        symbol: str,
        exit_price: float,
        exit_quantity: int | None = None,
        exit_reason: str | None = None,
        exit_at: str | None = None,
        fees: float = 0.0,
        raw_exit_price: float | None = None,
    ) -> PaperPosition | None:
        pos = self._positions.get(symbol)
        if not pos or pos.status != "OPEN":
            return None

        now_iso = exit_at or now_shanghai().isoformat()
        qty = exit_quantity if exit_quantity is not None else pos.quantity

        # Update position
        pos.current_price = exit_price
        pos.status = "CLOSED"
        pos.exit_reason = exit_reason
        pos.exit_at = now_iso
        pos.fees_paid += fees
        pos.gross_exit_value = exit_price * qty
        pos.raw_exit_price = raw_exit_price or exit_price

        # P&L
        gross_pnl = pos.gross_exit_value - pos.gross_entry_cost
        pos.pnl = gross_pnl - pos.fees_paid
        pos.pnl_pct = pos.pnl / pos.gross_entry_cost if pos.gross_entry_cost > 0 else 0.0

        # Holding days
        if pos.entry_at:
            try:
                entry_dt = parse_iso(pos.entry_at)
                exit_dt = parse_iso(now_iso)
                pos.holding_days = max(1, (exit_dt.date() - entry_dt.date()).days)
            except (ValueError, TypeError):
                pos.holding_days = 1

        # Record round trip
        rt = RoundTrip(
            round_trip_id=pos.round_trip_id or f"RT-{uuid.uuid4().hex[:12]}",
            symbol=symbol,
            pattern_id=pos.pattern_id,
            pattern_version=pos.pattern_version,
            entry_timestamp=pos.entry_at or "",
            exit_timestamp=now_iso,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=qty,
            gross_pnl=gross_pnl,
            net_pnl=pos.pnl,
            holding_days=pos.holding_days,
            exit_reason=exit_reason,
            regime_at_entry=pos.regime_at_entry,
            lifecycle_at_entry=pos.lifecycle_at_entry,
        )
        pos.round_trip_id = rt.round_trip_id
        self._round_trips.append(rt)

        self._save()
        return pos

    def partial_close(
        self,
        *,
        symbol: str,
        exit_price: float,
        exit_quantity: int,
        exit_reason: str | None = None,
        exit_at: str | None = None,
        fees: float = 0.0,
        raw_exit_price: float | None = None,
        method: str = "FIFO",
    ) -> PaperPosition | None:
        """Partially close a position using FIFO or specified lot matching."""
        pos = self._positions.get(symbol)
        if not pos or pos.status != "OPEN":
            return None
        if exit_quantity <= 0 or exit_quantity > pos.quantity:
            return None

        now_iso = exit_at or now_shanghai().isoformat()

        # FIFO: oldest lots first (simplified — single entry price for position)
        close_entry_cost = pos.entry_price * exit_quantity
        gross_exit_value = exit_price * exit_quantity
        gross_pnl = gross_exit_value - close_entry_cost

        # Update position
        remaining = pos.quantity - exit_quantity
        if remaining > 0:
            # Partial exit — keep position OPEN with reduced quantity
            pos.quantity = remaining
            pos.gross_entry_cost = pos.entry_price * remaining
            pos.current_price = exit_price
            pos.fees_paid += fees
            pos.raw_exit_price = raw_exit_price or exit_price

            # Record a closed round trip for the partial
            rt = RoundTrip(
                round_trip_id=f"RT-{uuid.uuid4().hex[:12]}",
                symbol=symbol,
                pattern_id=pos.pattern_id,
                pattern_version=pos.pattern_version,
                entry_timestamp=pos.entry_at or "",
                exit_timestamp=now_iso,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                quantity=exit_quantity,
                gross_pnl=gross_pnl,
                net_pnl=gross_pnl - fees,
                holding_days=1,
                exit_reason=exit_reason,
                regime_at_entry=pos.regime_at_entry,
                lifecycle_at_entry=pos.lifecycle_at_entry,
            )
            self._round_trips.append(rt)
        else:
            # Full exit
            self.close_position(
                symbol=symbol,
                exit_price=exit_price,
                exit_quantity=exit_quantity,
                exit_reason=exit_reason,
                exit_at=now_iso,
                fees=fees,
                raw_exit_price=raw_exit_price,
            )

        self._save()
        return self._positions.get(symbol)

    # ------------------------------------------------------------------
    # Position updates
    # ------------------------------------------------------------------

    def update_price(self, symbol: str, current_price: float, trade_date: str | None = None) -> None:
        """Update position with current market price for P&L / MAE / MFE."""
        pos = self._positions.get(symbol)
        if not pos or pos.status != "OPEN":
            return

        pos.current_price = current_price
        pos.pnl = (current_price - pos.entry_price) * pos.quantity - pos.fees_paid
        pos.pnl_pct = pos.pnl / pos.gross_entry_cost if pos.gross_entry_cost > 0 else 0.0

        # MAE/MFE
        if current_price > pos.mfe + pos.entry_price:
            pos.mfe = current_price - pos.entry_price
        if pos.entry_price - current_price > pos.mae:
            pos.mae = pos.entry_price - current_price

        # Holding days
        if pos.entry_at and trade_date:
            try:
                entry_dt = parse_iso(pos.entry_at)
                td = parse_iso(trade_date + "T15:00:00+08:00") if "T" not in trade_date else parse_iso(trade_date)
                pos.holding_days = max(1, (td.date() - entry_dt.date()).days)
            except (ValueError, TypeError):
                pass

    # ------------------------------------------------------------------
    # New trade date
    # ------------------------------------------------------------------

    def new_trade_date(self, trade_date: str, prev_trade_date: str | None = None) -> None:
        """Clear bought_today set for new trade date (T+1 enforcement)."""
        self._bought_today.clear()
        self._last_trade_date = trade_date

        # Update holding days for all open positions
        if prev_trade_date:
            try:
                prev_dt = parse_iso(prev_trade_date + "T15:00:00+08:00") if "T" not in prev_trade_date else parse_iso(prev_trade_date)
                cur_dt = parse_iso(trade_date + "T15:00:00+08:00") if "T" not in trade_date else parse_iso(trade_date)
                days_diff = (cur_dt.date() - prev_dt.date()).days
                if days_diff > 0:
                    for pos in self._positions.values():
                        if pos.status == "OPEN":
                            pos.holding_days = max(1, pos.holding_days + days_diff)
            except (ValueError, TypeError):
                pass

        self._save()

    # ------------------------------------------------------------------
    # Order tracking
    # ------------------------------------------------------------------

    def register_order(self, order: PaperOrder) -> None:
        self._orders[order.order_id] = order
        self._known_order_ids.add(order.order_id)
        self._save()

    def get_order(self, order_id: str) -> PaperOrder | None:
        return self._orders.get(order_id)

    def update_order(self, order_id: str, **updates: Any) -> PaperOrder | None:
        order = self._orders.get(order_id)
        if not order:
            return None
        for k, v in updates.items():
            if hasattr(order, k):
                setattr(order, k, v)
        self._save()
        return order

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_position(self, symbol: str) -> PaperPosition | None:
        return self._positions.get(symbol)

    def list_positions(self, status: str | None = None) -> list[PaperPosition]:
        if status:
            return [p for p in self._positions.values() if p.status == status]
        return list(self._positions.values())

    def list_round_trips(self, limit: int = 100) -> list[RoundTrip]:
        return list(reversed(self._round_trips))[:limit]

    def list_orders(self, status: str | None = None) -> list[PaperOrder]:
        if status:
            return [o for o in self._orders.values() if o.status == status]
        return list(self._orders.values())

    @property
    def bought_today(self) -> set[str]:
        return self._bought_today

    @property
    def open_order_count(self) -> int:
        return sum(1 for o in self._orders.values() if o.status in ("CREATED", "SUBMITTED", "PARTIAL_FILLED"))

    # ------------------------------------------------------------------
    # Cash accounting
    # ------------------------------------------------------------------

    def cash_balance(self, initial_cash: float) -> float:
        """Calculate current cash balance from initial cash minus buys + sells."""
        cash = initial_cash
        for o in self._orders.values():
            if o.status == "FILLED" and o.filled_price is not None:
                value = o.filled_price * o.filled_quantity
                if o.direction == "BUY":
                    cash -= value
                else:
                    cash += value
        return cash

    def total_market_value(self) -> float:
        """Current market value of all open positions."""
        return sum(p.current_price * p.quantity for p in self._positions.values() if p.status == "OPEN")

    def total_pnl(self) -> float:
        return sum(p.pnl for p in self._positions.values())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        if not self._persist_path:
            return
        data = {
            "positions": [p.to_dict() for p in self._positions.values()],
            "round_trips": [rt.to_dict() for rt in self._round_trips],
            "orders": [o.to_dict() for o in self._orders.values()],
            "bought_today": list(self._bought_today),
            "last_trade_date": self._last_trade_date,
        }
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._persist_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
        tmp.replace(self._persist_path)

    def load(self) -> None:
        """Restore state from persisted snapshot."""
        if not self._persist_path or not self._persist_path.exists():
            return
        with open(self._persist_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        self._positions.clear()
        for pdata in data.get("positions", []):
            pos = PaperPosition(**{k: v for k, v in pdata.items() if k in PaperPosition.__dataclass_fields__})
            self._positions[pos.symbol] = pos

        self._round_trips.clear()
        for rdata in data.get("round_trips", []):
            rt = RoundTrip(**{k: v for k, v in rdata.items() if k in RoundTrip.__dataclass_fields__})
            self._round_trips.append(rt)

        self._orders.clear()
        for odata in data.get("orders", []):
            order = PaperOrder(**{k: v for k, v in odata.items() if k in PaperOrder.__dataclass_fields__})
            self._orders[order.order_id] = order
            self._known_order_ids.add(order.order_id)

        self._bought_today = set(data.get("bought_today", []))
        self._last_trade_date = data.get("last_trade_date")

    def detect_unknown_orders(self) -> list[str]:
        """Return order_ids that were persisted but are not in the current session."""
        return [oid for oid in self._known_order_ids if oid not in self._orders]

    def corporate_action_adjust(self, symbol: str, dividend_per_share: float = 0.0) -> None:
        """Adjust position P&L for cash dividends."""
        pos = self._positions.get(symbol)
        if not pos or pos.status != "OPEN":
            return
        total_dividend = dividend_per_share * pos.quantity
        # Adjust P&L (dividends increase P&L)
        pos.pnl += total_dividend
        pos.gross_entry_cost -= total_dividend  # effectively lowers cost basis
        self._save()
