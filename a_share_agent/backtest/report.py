from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any


def _pct(v: Any) -> str:
    try:return f"{float(v)*100:.2f}%"
    except:return "-"

def _num(v: Any) -> str:
    try:return f"{float(v):,.2f}"
    except:return "-"


class BacktestReportWriter:
    def __init__(self, root: Path):
        self.root=Path(root)/"data"/"backtest"/"runs"; self.root.mkdir(parents=True,exist_ok=True)

    def write(self, report: dict[str,Any]) -> Path:
        run_id=str(report["run_id"]); path=self.root/run_id; path.mkdir(parents=True,exist_ok=True)
        (path/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        for name,key in (("trades.csv","trades"),("equity_curve.csv","equity_curve"),("monthly_returns.csv","monthly_returns"),("rejections.csv","rejections")):
            rows=report.get(key,[])
            if rows:
                fields=[]
                for r in rows:
                    for k in r:
                        if k not in fields: fields.append(k)
                with (path/name).open("w",encoding="utf-8-sig",newline="") as fh:
                    w=csv.DictWriter(fh,fieldnames=fields,extrasaction="ignore"); w.writeheader()
                    for r in rows:
                        w.writerow({k:(json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v) for k,v in r.items()})
        (path/"report.html").write_text(self._html(report),encoding="utf-8")
        latest=self.root/"latest.json"; latest.write_text(json.dumps({"run_id":run_id,"path":str(path)},ensure_ascii=False,indent=2),encoding="utf-8")
        return path

    def list_runs(self, limit: int=100) -> list[dict[str,Any]]:
        out=[]
        for p in sorted(self.root.glob("bt-*"),key=lambda x:x.stat().st_mtime,reverse=True)[:limit]:
            f=p/"report.json"
            if not f.exists():continue
            try:
                r=json.loads(f.read_text(encoding="utf-8")); out.append({"run_id":r.get("run_id"),"created_at":r.get("created_at"),"settings":r.get("settings"),"metrics":r.get("metrics"),"coverage":r.get("coverage")})
            except Exception: pass
        return out

    def load(self, run_id: str) -> dict[str,Any] | None:
        p=self.root/run_id/"report.json"
        if not p.exists(): return None
        return json.loads(p.read_text(encoding="utf-8"))

    def _html(self,r:dict[str,Any])->str:
        m=r.get("metrics",{}); months=r.get("monthly_returns",[]); trades=[x for x in r.get("trades",[]) if x.get("direction")=="SELL"]
        cards=[("总收益",_pct(m.get("total_return"))),("CAGR",_pct(m.get("cagr"))),("最大回撤",_pct(m.get("max_drawdown"))),("Sharpe",_num(m.get("sharpe"))),("胜率",_pct(m.get("win_rate"))),("Profit Factor",_num(m.get("profit_factor"))),("交易数",str(m.get("closed_trades",0))),("总费用",_num(m.get("total_fees")))]
        card_html="".join(f'<div class="card"><b>{html.escape(k)}</b><span>{html.escape(v)}</span></div>' for k,v in cards)
        month_rows="".join(f'<tr><td>{x["month"]}</td><td>{_pct(x["return"])}</td><td>{_num(x["ending_equity"])}</td></tr>' for x in months)
        trade_rows="".join(f'<tr><td>{html.escape(str(x.get("trade_date")))}</td><td>{html.escape(str(x.get("symbol")))}</td><td>{html.escape(str(x.get("strategy_id")))}</td><td>{_pct(x.get("pnl_pct"))}</td><td>{html.escape(str(x.get("exit_reason")))}</td></tr>' for x in trades[-100:])
        eq=json.dumps([[x["date"],x["equity"]] for x in r.get("equity_curve",[])],ensure_ascii=False)
        settings=r.get("settings",{})
        dq=r.get("data_quality",{})
        return f'''<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(str(settings.get("report_title","Backtest")))}</title><style>
body{{font-family:system-ui,-apple-system,sans-serif;background:#0b1020;color:#e8ecf4;margin:0;padding:24px}}h1,h2{{margin:8px 0 16px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px}}.card{{background:#141c31;padding:16px;border-radius:12px;display:flex;flex-direction:column;gap:8px}}.card span{{font-size:24px}}table{{width:100%;border-collapse:collapse;background:#10182a}}th,td{{padding:8px 10px;border-bottom:1px solid #26324d;text-align:left}}.warn{{background:#332611;padding:12px;border-radius:10px}}canvas{{width:100%;height:320px;background:#10182a;border-radius:12px}}</style></head><body>
<h1>{html.escape(str(settings.get("report_title","A股 Agent 回测")))}</h1><p>Run: {html.escape(str(r.get("run_id")))}</p><div class="grid">{card_html}</div>
<h2>净值曲线</h2><canvas id="c" width="1200" height="320"></canvas>
<h2>月度收益</h2><table><tr><th>月份</th><th>收益</th><th>期末权益</th></tr>{month_rows}</table>
<h2>最近100笔平仓</h2><table><tr><th>日期</th><th>股票</th><th>策略</th><th>收益率</th><th>退出</th></tr>{trade_rows}</table>
<h2>数据质量</h2><pre class="warn">{html.escape(json.dumps(dq,ensure_ascii=False,indent=2))}</pre>
<script>const d={eq}; const c=document.getElementById('c'),x=c.getContext('2d'); if(d.length>1){{let vals=d.map(z=>z[1]),mn=Math.min(...vals),mx=Math.max(...vals);x.strokeStyle='#7aa2ff';x.lineWidth=2;x.beginPath();d.forEach((z,i)=>{{let px=20+i*(c.width-40)/(d.length-1),py=c.height-20-(z[1]-mn)*(c.height-40)/(mx-mn||1);if(i===0)x.moveTo(px,py);else x.lineTo(px,py)}});x.stroke();}}</script></body></html>'''
