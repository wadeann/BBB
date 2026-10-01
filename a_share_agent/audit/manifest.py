from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import RuntimeConfig
from ..utils import now_shanghai
from .replay import ReplayEngine


class DailyManifestBuilder:
    def __init__(self, config: RuntimeConfig, replay: ReplayEngine):
        self.config = config
        self.replay = replay

    def build(self, trade_date: str) -> dict[str, Any]:
        events = self.replay.load_day(trade_date)
        verify = self.replay.verify_hash_chain(trade_date)
        counts = {k: 0 for k in ["universe", "hard_filter_pass", "deep_dive", "entry", "watch", "reject"]}
        exec_summary = {"submitted": 0, "filled": 0, "cancelled": 0, "rejected": 0}
        review_id = None
        review_path = None
        system_errors = []
        market_summary: dict[str, Any] = {}
        risk_summary = {"pass": 0, "reject": 0}
        pnl_summary: dict[str, Any] = {}
        for e in events:
            typ, payload = e.get("event_type"), e.get("payload") or {}
            if typ == "CANDIDATE_BATCH" and isinstance(payload, dict):
                counts["universe"] = max(counts["universe"], int(payload.get("universe_count", len(payload.get("symbols", [])))))
                counts["hard_filter_pass"] = max(counts["hard_filter_pass"], int(payload.get("hard_filter_pass", len(payload.get("symbols", [])))))
            elif typ == "DEEP_DIVE_REPORT":
                counts["deep_dive"] += 1
            elif typ == "SIGNAL_DECISION" and isinstance(payload, dict):
                d = payload.get("decision")
                if d in {"ENTRY_CANDIDATE", "ORDER_PROPOSAL"}: counts["entry"] += 1
                elif d == "WATCH": counts["watch"] += 1
                elif d == "REJECT": counts["reject"] += 1
            elif typ == "RISK_RESULT" and isinstance(payload, dict):
                status = str(payload.get("status", payload.get("decision", ""))).upper()
                risk_summary["pass" if status == "PASS" else "reject"] += 1
            elif typ == "ORDER_REQUEST": exec_summary["submitted"] += 1
            elif typ == "EXECUTION_RECEIPT" and isinstance(payload, dict):
                s = str(payload.get("status", "")).upper()
                if "FILL" in s: exec_summary["filled"] += 1
                elif "CANCEL" in s: exec_summary["cancelled"] += 1
                elif "REJECT" in s: exec_summary["rejected"] += 1
            elif typ == "MARKET_CONTEXT" and isinstance(payload, dict): market_summary = payload
            elif typ == "PNL_SNAPSHOT" and isinstance(payload, dict): pnl_summary = payload
            elif typ == "DAILY_REVIEW" and isinstance(payload, dict):
                review_id = payload.get("review_id")
                review_path = payload.get("review_path")
            elif e.get("status") == "error" or typ in {"SYSTEM_ERROR", "AUDIT_GAP"}:
                system_errors.append({"event_id": e.get("event_id"), "event_type": typ, "error_code": e.get("error_code")})
        manifest = {
            "schema_version": "5.0.0",
            "trade_date": trade_date,
            "generated_at": now_shanghai().isoformat(),
            "market_summary": market_summary,
            "candidate_funnel": counts,
            "risk_summary": risk_summary,
            "execution_summary": exec_summary,
            "pnl_summary": pnl_summary,
            "review": {"review_id": review_id, "review_path": review_path},
            "event_count": len(events),
            "first_event_hash": events[0]["event_hash"] if events else None,
            "last_event_hash": events[-1]["event_hash"] if events else None,
            "versions": {
                "strategy_version": self.config.runtime.get("strategy_version", "skill-v5.0.0"),
                "config_hash": self.config.config_hash,
                "model_id": self.config.runtime.get("model_id", "external-llm"),
                "code_version": self.config.runtime.get("code_version", "runtime-v0.1.0"),
            },
            "data_quality": {"hash_chain_ok": bool(verify.get("ok"))},
            "system_errors": system_errors,
        }
        y, m, d = trade_date.split("-")
        path = self.replay.audit_root / y / m / d / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return manifest
