from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any, Callable

from .models import BacktestSettings


def _add_months(s: str, months: int) -> str:
    d=date.fromisoformat(s); y=d.year+(d.month-1+months)//12; m=(d.month-1+months)%12+1
    import calendar
    day=min(d.day,calendar.monthrange(y,m)[1])
    return date(y,m,day).isoformat()


class WalkForwardEngine:
    """Threshold walk-forward optimization.

    Training selects the score threshold with the best Calmar among candidates that
    have a minimum trade count; the immediately following test window is untouched.
    """
    def __init__(self, run_fn: Callable[[BacktestSettings],dict[str,Any]]): self.run_fn=run_fn

    def run(self, base: BacktestSettings, *, train_months: int=12, test_months: int=3,
            thresholds: list[float] | None=None, min_trades: int=5) -> dict[str,Any]:
        thresholds=thresholds or [72,75,78,80,83]
        cursor=base.start_date; segments=[]
        while True:
            test_start=_add_months(cursor,train_months)
            train_end=(date.fromisoformat(test_start)-timedelta(days=1)).isoformat()
            test_end=_add_months(test_start,test_months)
            if test_start>=base.end_date: break
            if test_end>base.end_date: test_end=base.end_date
            trials=[]
            for t in thresholds:
                r=self.run_fn(replace(base,start_date=cursor,end_date=train_end,min_score=float(t)))
                m=r.get("metrics",{}); trades=int(m.get("closed_trades",0)); score=float(m.get("calmar",0)) if trades>=min_trades else -999
                trials.append({"threshold":t,"score":score,"metrics":m})
            best=max(trials,key=lambda x:x["score"])
            test=self.run_fn(replace(base,start_date=test_start,end_date=test_end,min_score=float(best["threshold"])))
            segments.append({"train":{"start":cursor,"end":train_end},"test":{"start":test_start,"end":test_end},"selected_threshold":best["threshold"],"training_trials":trials,"test_metrics":test.get("metrics",{}),"test_run_id":test.get("run_id")})
            cursor=_add_months(cursor,test_months)
            if test_end>=base.end_date: break
        return {"method":"rolling_walk_forward","segments":segments,"threshold_grid":thresholds,"train_months":train_months,"test_months":test_months}
