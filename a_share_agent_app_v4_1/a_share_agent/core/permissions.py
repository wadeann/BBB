from __future__ import annotations

from typing import Any


class PermissionDenied(RuntimeError):
    pass


class PhasePermissions:
    def __init__(self, permissions_cfg: dict[str, Any]):
        self.phases = permissions_cfg.get("phases", {})

    def for_phase(self, phase: str) -> dict[str, Any]:
        if phase not in self.phases:
            raise PermissionDenied(f"unknown or unconfigured phase: {phase}")
        return dict(self.phases[phase])

    def require(self, phase: str, action: str, *, direction: str | None = None) -> None:
        p = self.for_phase(phase)
        if action == "new_entry" and not p.get("new_entry", False):
            raise PermissionDenied(f"new entry forbidden in {phase}")
        if action == "place_order" and not p.get("place_order", False):
            raise PermissionDenied(f"order placement forbidden in {phase}")
        if action == "cancel_order" and not p.get("cancel_order", False):
            raise PermissionDenied(f"cancel forbidden in {phase}")
        if action == "reduce_position" and not p.get("reduce_position", False):
            raise PermissionDenied(f"position reduction forbidden in {phase}")
        if p.get("order_scope") == "reduce_only" and direction and direction.upper() == "BUY":
            raise PermissionDenied(f"phase {phase} only allows reduce-only orders")

    def allowed_actions(self, phase: str) -> list[str]:
        p = self.for_phase(phase)
        return [k for k in ("new_entry", "reduce_position", "cancel_order", "place_order") if p.get(k) is True]

    def forbidden_actions(self, phase: str) -> list[str]:
        p = self.for_phase(phase)
        return [k for k in ("new_entry", "reduce_position", "cancel_order", "place_order") if p.get(k) is False]
