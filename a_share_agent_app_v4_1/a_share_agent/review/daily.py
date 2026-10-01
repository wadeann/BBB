from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from ..audit.replay import ReplayEngine
from ..config import RuntimeConfig
from ..utils import now_shanghai


class DailyReviewer:
    def __init__(self, config: RuntimeConfig, replay: ReplayEngine):
        self.config, self.replay = config, replay

    def build(self, trade_date: str) -> dict[str, Any]:
        events = self.replay.load_day(trade_date)
        counts: dict[str, int] = {}
        strategy_stats: dict[str, dict[str, int]] = {}
        system_errors = []
        for e in events:
            typ = e.get("event_type", "UNKNOWN"); counts[typ] = counts.get(typ, 0)+1
            sid = e.get("strategy_id")
            if sid:
                s = strategy_stats.setdefault(sid, {"signals":0,"risk_pass":0,"risk_reject":0,"orders":0,"fills":0})
                if typ == "SIGNAL_DECISION": s["signals"] += 1
                elif typ == "RISK_RESULT":
                    p=e.get("payload") or {}; st=str(p.get("status","")).upper(); s["risk_pass" if st=="PASS" else "risk_reject"] += 1
                elif typ == "ORDER_REQUEST": s["orders"] += 1
                elif typ == "EXECUTION_RECEIPT": s["fills"] += 1
            if e.get("status") == "error": system_errors.append(e.get("event_id"))
        review_id = f"REV-{trade_date.replace('-','')}-{uuid.uuid4().hex[:8]}"
        report = {
            "review_id": review_id,
            "trading_date": trade_date,
            "production_version": {
                "strategy_version": self.config.runtime.get("strategy_version", "skill-v5.0.0"),
                "config_hash": self.config.config_hash,
                "code_version": self.config.runtime.get("code_version", "runtime-v0.1.0"),
            },
            "summary": f"审计事件 {len(events)} 条；系统错误 {len(system_errors)} 条。该报告为确定性基础复盘，策略归因可再交给外部 LLM。",
            "metrics": {"event_count": len(events), "event_type_counts": counts, "strategy_stats": strategy_stats},
            "attribution": [],
            "errors": [{"event_id": x, "category":"SYSTEM_ERROR"} for x in system_errors],
            "change_proposals": [],
            "production_config_changed": False,
        }
        y,m,d=trade_date.split("-")
        path = self.replay.audit_root / y / m / d / "reviews" / f"{review_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["review_path"] = str(path)
        return report
