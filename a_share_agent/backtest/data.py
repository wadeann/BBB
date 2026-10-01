from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..mcp.base import MCPInvoker


def _date_str(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    s = s[:10].replace("/", "-")
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        return s


def normalize_bars(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        for key in ("data", "bars", "klines", "items", "result", "list"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        d = _date_str(x.get("date") or x.get("trade_date") or x.get("time") or x.get("datetime"))
        if not d:
            continue
        def f(*keys: str, default: float = 0.0) -> float:
            for k in keys:
                if x.get(k) is not None:
                    try: return float(x[k])
                    except Exception: pass
            return default
        row = {
            "date": d,
            "open": f("open", "o"),
            "high": f("high", "h"),
            "low": f("low", "l"),
            "close": f("close", "c"),
            "volume": f("volume", "vol", "v"),
            "amount": f("amount", "turnover", default=0.0),
            "pct": f("pct", "pct_chg", "change_pct", default=0.0),
        }
        if row["open"] > 0 and row["close"] > 0:
            out.append(row)
    uniq = {x["date"]: x for x in out}
    return [uniq[k] for k in sorted(uniq)]


@dataclass
class UniverseInfo:
    symbols: list[str]
    source: str
    survivorship_bias: bool
    notes: list[str]


class HistoricalDataProvider:
    """Historical daily data layer with on-disk cache.

    The provider never calls present-day market-health/mainline tools to reconstruct
    the past. Market and sector regimes are derived from historical price series.
    """
    def __init__(self, root: Path, mcp: MCPInvoker | None = None, *, use_cache: bool = True):
        self.root = Path(root)
        self.mcp = mcp
        self.use_cache = use_cache
        self.cache_root = self.root / "data" / "backtest" / "cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._bars_mem: dict[str, list[dict[str, Any]]] = {}
        self._sector_mem: dict[str, dict[str, Any]] = {}
        self.warnings: list[str] = []

    @staticmethod
    def _safe(symbol: str) -> str:
        return symbol.replace("/", "_").replace(".", "_").replace(":", "_")

    def _cache_file(self, kind: str, key: str) -> Path:
        p = self.cache_root / kind
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{self._safe(key)}.json"

    def _load_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_json(self, path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def load_universe(self, universe_file: str, *, max_universe: int = 0) -> UniverseInfo:
        path = (self.root / universe_file).resolve() if not Path(universe_file).is_absolute() else Path(universe_file)
        syms: list[str] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                s = line.strip().split(",")[0].strip()
                if s and not s.startswith("#") and any(ch.isdigit() for ch in s):
                    syms.append(s)
            source = f"file:{path}"
            survivorship = False
            notes = ["Universe supplied by user file. Point-in-time membership is the user's responsibility."]
        elif self.mcp is not None:
            target=max_universe or 5000; page=1; page_size=min(50,target)
            while len(syms)<target and page<=120:
                raw = self.mcp.invoke("mcp_intel_wencai_search", query="A股 非ST 非退市 非停牌 上市超过20个交易日", page=page, limit=page_size)
                items = raw.get("data", raw) if isinstance(raw, dict) else raw
                if not isinstance(items, list) or not items: break
                before=len(syms)
                for x in items:
                    if isinstance(x, str): syms.append(x)
                    elif isinstance(x, dict) and x.get("symbol"): syms.append(str(x["symbol"]))
                syms=list(dict.fromkeys(syms))
                if len(items)<page_size or len(syms)==before: break
                page+=1
            source = "mcp_current_universe"
            survivorship = True
            notes = ["Current-stock universe used for historical test: survivorship bias is possible.", f"Paged wencai universe retrieval; requested cap={target}."]
        else:
            source = "none"; survivorship = True; notes = ["No universe source available."]
        syms = list(dict.fromkeys(syms))
        if max_universe and max_universe > 0:
            syms = syms[:max_universe]
        return UniverseInfo(syms, source, survivorship, notes)

    def bars(self, symbol: str, *, count: int = 900) -> list[dict[str, Any]]:
        if symbol in self._bars_mem:
            return self._bars_mem[symbol]
        path = self._cache_file("bars", symbol)
        if self.use_cache and path.exists():
            rows = normalize_bars(self._load_json(path))
            if rows:
                self._bars_mem[symbol] = rows
                return rows
        # Optional local CSV has priority over MCP for reproducible tests.
        csv_path = self.root / "data" / "backtest" / "prices" / f"{self._safe(symbol)}.csv"
        if csv_path.exists():
            with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
                rows = normalize_bars(list(csv.DictReader(fh)))
        elif self.mcp is not None:
            try:
                raw = self.mcp.invoke("mcp_intel_tdx_kline", symbol=symbol, period="D", count=count)
                rows = normalize_bars(raw)
            except Exception:
                raw = self.mcp.invoke("mcp_intel_fetch_kline", symbol=symbol, period="D", count=count)
                rows = normalize_bars(raw)
        else:
            rows = []
        self._bars_mem[symbol] = rows
        if rows and self.use_cache:
            self._save_json(path, rows)
        if not rows:
            self.warnings.append(f"NO_BARS:{symbol}")
        return rows

    def sector_info(self, symbol: str) -> dict[str, Any]:
        if symbol in self._sector_mem:
            return self._sector_mem[symbol]
        path = self._cache_file("sector_map", symbol)
        if self.use_cache and path.exists():
            val = self._load_json(path)
            if isinstance(val, dict):
                self._sector_mem[symbol] = val
                return val
        val: dict[str, Any] = {"name": None, "code": None, "source": "unknown"}
        if self.mcp is not None:
            try:
                raw = self.mcp.invoke("mcp_intel_tdx_f10", symbol=symbol, module="basic")
                if isinstance(raw, dict):
                    base = raw.get("basic") if isinstance(raw.get("basic"), dict) else raw
                    val["name"] = base.get("industry") or base.get("sector") or base.get("industry_name") or raw.get("industry")
                    val["code"] = base.get("industry_code") or base.get("sector_code") or base.get("hy_code") or raw.get("industry_code") or raw.get("sector_code")
                    val["source"] = "current_f10"
            except Exception as exc:
                self.warnings.append(f"SECTOR_LOOKUP_FAILED:{symbol}:{type(exc).__name__}")
        self._sector_mem[symbol] = val
        if self.use_cache:
            self._save_json(path, val)
        return val

    def sector_bars(self, code: str | None) -> list[dict[str, Any]]:
        if not code:
            return []
        key = f"sector:{code}"
        if key in self._bars_mem:
            return self._bars_mem[key]
        path = self._cache_file("sectors", code)
        if self.use_cache and path.exists():
            rows = normalize_bars(self._load_json(path))
            if rows:
                self._bars_mem[key] = rows; return rows
        rows: list[dict[str, Any]] = []
        if self.mcp is not None:
            try:
                rows = normalize_bars(self.mcp.invoke("mcp_intel_fetch_sector_history", code=code))
            except Exception as exc:
                self.warnings.append(f"SECTOR_BARS_FAILED:{code}:{type(exc).__name__}")
        self._bars_mem[key] = rows
        if rows and self.use_cache: self._save_json(path, rows)
        return rows
