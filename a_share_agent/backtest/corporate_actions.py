"""Corporate Action processing layer for point-in-time portfolio execution.

Ensures execution cash flows and share holdings match real historical exchange events
without future adjustment lookahead bias.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .portfolio import Portfolio

logger = logging.getLogger(__name__)


@dataclass
class CorporateAction:
    symbol: str
    ex_date: str
    record_date: str
    action_type: str  # cash_dividend, bonus_shares, stock_dividend, split, rights_issue
    cash_dividend_per_share: float = 0.0
    bonus_ratio: float = 0.0
    stock_dividend_ratio: float = 0.0
    split_ratio: float = 1.0
    rights_ratio: float = 0.0
    rights_price: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CorporateAction:
        def f(key: str, default: float = 0.0) -> float:
            try:
                v = d.get(key)
                return float(v) if v not in (None, "", "--") else default
            except Exception:
                return default

        return cls(
            symbol=str(d.get("symbol") or ""),
            ex_date=str(d.get("ex_date") or ""),
            record_date=str(d.get("record_date") or ""),
            action_type=str(d.get("action_type") or "cash_dividend"),
            cash_dividend_per_share=f("cash_dividend_per_share", 0.0),
            bonus_ratio=f("bonus_ratio", 0.0),
            stock_dividend_ratio=f("stock_dividend_ratio", 0.0),
            split_ratio=f("split_ratio", 1.0),
            rights_ratio=f("rights_ratio", 0.0),
            rights_price=f("rights_price", 0.0),
        )


class CorporateActionEngine:
    """Manages and executes corporate actions point-in-time on ex-dates."""

    def __init__(self, actions: list[CorporateAction] | None = None):
        self._actions_by_date: dict[str, list[CorporateAction]] = {}
        self.logs: list[dict[str, Any]] = []
        if actions:
            for act in actions:
                self.add_action(act)

    def add_action(self, action: CorporateAction) -> None:
        if action.ex_date:
            self._actions_by_date.setdefault(action.ex_date, []).append(action)

    @classmethod
    def load_from_file(cls, path: Path | str) -> CorporateActionEngine:
        p = Path(path)
        engine = cls()
        if not p.exists():
            return engine
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                sym = row.get("symbol")
                ex_date = row.get("ex_date")
                if sym and ex_date:
                    engine.add_action(CorporateAction.from_dict(row))
        return engine

    def actions_on(self, date: str) -> list[CorporateAction]:
        return self._actions_by_date.get(date, [])

    def process_actions(self, date: str, portfolio: Portfolio) -> list[dict[str, Any]]:
        """Process any corporate actions occurring on ex_date for held positions."""
        actions = self.actions_on(date)
        applied = []
        for act in actions:
            pos = portfolio.positions.get(act.symbol)
            if not pos:
                continue

            # 1. Cash dividend (派息)
            if act.cash_dividend_per_share > 0:
                payout = round(act.cash_dividend_per_share * pos.quantity, 2)
                portfolio.cash = float(portfolio.cash or 0.0) + payout
                event = {
                    "date": date,
                    "symbol": act.symbol,
                    "type": "CASH_DIVIDEND",
                    "cash_received": payout,
                    "dividend_per_share": act.cash_dividend_per_share,
                    "shares_held": pos.quantity,
                }
                applied.append(event)
                self.logs.append(event)
                logger.info("corporate_action_cash_dividend: %s payout=%.2f", act.symbol, payout)

            # 2. Stock dividend & Bonus shares (送红股 / 转增股本)
            ratio = act.bonus_ratio + act.stock_dividend_ratio
            if ratio > 0:
                old_qty = pos.quantity
                new_qty = int(round(old_qty * (1.0 + ratio)))
                pos.quantity = new_qty
                # Adjust cost basis and stops
                pos.entry_price = round(pos.entry_price / (1.0 + ratio), 4)
                if pos.stop_price > 0:
                    pos.stop_price = round(pos.stop_price / (1.0 + ratio), 4)
                pos.highest_price = round(pos.highest_price / (1.0 + ratio), 4)
                if pos.lowest_price:
                    pos.lowest_price = round(pos.lowest_price / (1.0 + ratio), 4)
                event = {
                    "date": date,
                    "symbol": act.symbol,
                    "type": "STOCK_DIVIDEND_OR_BONUS",
                    "old_quantity": old_qty,
                    "new_quantity": new_qty,
                    "bonus_ratio": ratio,
                }
                applied.append(event)
                self.logs.append(event)
                logger.info("corporate_action_bonus_shares: %s old_qty=%d new_qty=%d", act.symbol, old_qty, new_qty)

            # 3. Stock split (拆股)
            if act.split_ratio > 1.0:
                old_qty = pos.quantity
                new_qty = int(round(old_qty * act.split_ratio))
                pos.quantity = new_qty
                pos.entry_price = round(pos.entry_price / act.split_ratio, 4)
                if pos.stop_price > 0:
                    pos.stop_price = round(pos.stop_price / act.split_ratio, 4)
                pos.highest_price = round(pos.highest_price / act.split_ratio, 4)
                if pos.lowest_price:
                    pos.lowest_price = round(pos.lowest_price / act.split_ratio, 4)
                event = {
                    "date": date,
                    "symbol": act.symbol,
                    "type": "STOCK_SPLIT",
                    "old_quantity": old_qty,
                    "new_quantity": new_qty,
                    "split_ratio": act.split_ratio,
                }
                applied.append(event)
                self.logs.append(event)

        return applied
