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


def _extract_sector_fields(raw: Any) -> tuple[str | None, str | None, dict[str, Any]]:
    """Best-effort parser for heterogeneous TDX F10 payloads."""
    name_keys = {"industry","sector","industry_name","sector_name","hy_name","hyname","所属行业","行业","行业名称","板块","板块名称"}
    code_keys = {"industry_code","sector_code","hy_code","hycode","industrycode","行业代码","板块代码"}
    name = None
    code = None
    visited: set[str] = set()
    def walk(obj: Any, depth: int = 0) -> None:
        nonlocal name, code
        if depth > 5 or (name is not None and code is not None):
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                ks = str(k); lk = ks.lower(); visited.add(ks)
                if name is None and (lk in name_keys or ks in name_keys) and isinstance(v, (str,int,float)) and str(v).strip():
                    name = str(v)
                if code is None and (lk in code_keys or ks in code_keys) and isinstance(v, (str,int,float)) and str(v).strip():
                    code = str(v)
            for v in obj.values():
                if isinstance(v, (dict,list)): walk(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj[:50]:
                if isinstance(v, (dict,list)): walk(v, depth + 1)
    walk(raw)
    diag = {"top_level_keys": sorted(str(k) for k in raw.keys())[:30] if isinstance(raw, dict) else [], "visited_key_sample": sorted(visited)[:50]}
    return name, code, diag


@dataclass
class UniverseInfo:
    symbols: list[str]
    source: str
    survivorship_bias: bool
    notes: list[str]
    point_in_time: bool = False
    membership_records: int = 0


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
        self._membership: dict[str, list[dict[str, Any]]] = {}

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

    def _normalize_membership_items(self, raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, dict):
            for key in ("data", "items", "symbols", "securities", "result", "list"):
                if isinstance(raw.get(key), list):
                    raw = raw[key]; break
        if not isinstance(raw, list):
            return []
        def as_bool(v: Any, default: bool=False) -> bool:
            if v is None: return default
            if isinstance(v,bool): return v
            if isinstance(v,(int,float)): return v != 0
            return str(v).strip().lower() in {"1","true","yes","y","on"}
        out=[]
        for x in raw:
            if isinstance(x, str):
                out.append({"symbol":x,"active_from":"","active_to":"","tradable":True})
                continue
            if not isinstance(x, dict) or not x.get("symbol"):
                continue
            start=_date_str(x.get("active_from") or x.get("effective_from") or x.get("listing_date") or x.get("start_date"))
            end=_date_str(x.get("active_to") or x.get("effective_to") or x.get("delisting_date") or x.get("end_date"))
            out.append({
                "symbol":str(x["symbol"]),"active_from":start,"active_to":end,
                "tradable":as_bool(x.get("tradable"), True),"st":as_bool(x.get("st", x.get("risk_warning", False))),
                "suspended":as_bool(x.get("suspended", False)),"board":x.get("board"),"raw":x,
            })
        return out

    def _load_local_security_master(self, start_date: str, end_date: str) -> UniverseInfo | None:
        path=self.root/"data"/"backtest"/"security_master.csv"
        if not path.exists():
            return None
        with path.open("r",encoding="utf-8-sig",newline="") as fh:
            items=self._normalize_membership_items(list(csv.DictReader(fh)))
        if not items:
            return None
        symbols=[]
        for rec in items:
            sym=rec["symbol"]; symbols.append(sym); self._membership.setdefault(sym,[]).append(rec)
        return UniverseInfo(list(dict.fromkeys(symbols)),f"security_master:{path}",False,["Local point-in-time security master used."],True,len(items))

    def load_universe_for_period(self, start_date: str, end_date: str, universe_file: str, *, max_universe: int = 0, mode: str = "prefer_point_in_time") -> UniverseInfo:
        if mode in {"strict_point_in_time","prefer_point_in_time"}:
            local=self._load_local_security_master(start_date,end_date)
            if local:
                if max_universe: local.symbols=local.symbols[:max_universe]
                return local
            if self.mcp is not None:
                try:
                    raw=self.mcp.invoke("mcp_intel_get_historical_universe",start_date=start_date,end_date=end_date,mode="membership_intervals",include_status=True)
                    items=self._normalize_membership_items(raw)
                    interval_items=[x for x in items if x.get("active_from") or x.get("active_to")]
                    if items and interval_items:
                        symbols=[]
                        for rec in items:
                            sym=rec["symbol"]; symbols.append(sym); self._membership.setdefault(sym,[]).append(rec)
                        syms=list(dict.fromkeys(symbols))
                        if max_universe: syms=syms[:max_universe]
                        return UniverseInfo(syms,"mcp_historical_universe",False,["Point-in-time membership intervals returned by Intel MCP."],True,len(items))
                    if items:
                        self.warnings.append("HISTORICAL_UNIVERSE_NO_INTERVALS")
                except Exception as exc:
                    self.warnings.append(f"HISTORICAL_UNIVERSE_UNAVAILABLE:{type(exc).__name__}")
            if mode == "strict_point_in_time":
                raise RuntimeError("strict point-in-time universe requested, but neither security_master.csv nor historical universe MCP intervals are available")
        info=self.load_universe(universe_file,max_universe=max_universe)
        info.notes.append(f"universe_mode={mode}; point-in-time source unavailable, fallback used")
        return info

    def eligible_on(self, symbol: str, as_of: str) -> bool:
        records=self._membership.get(symbol)
        if not records:
            return True
        for r in records:
            start=str(r.get("active_from") or "")
            end=str(r.get("active_to") or "")
            if start and as_of < start: continue
            if end and as_of > end: continue
            if not bool(r.get("tradable",True)) or bool(r.get("st",False)) or bool(r.get("suspended",False)):
                continue
            return True
        return False

    def load_universe(self, universe_file: str, *, max_universe: int = 0) -> UniverseInfo:
        path = (self.root / universe_file).resolve() if not Path(universe_file).is_absolute() else Path(universe_file)
        syms: list[str] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                s = line.strip().split(",")[0].strip()
                if s and not s.startswith("#") and any(ch.isdigit() for ch in s):
                    syms.append(s)
            source = f"file:{path}"
            survivorship = True
            notes = ["Static universe file used. This is not point-in-time membership unless the file itself was constructed point-in-time."]
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
            # v0.6.1 re-parses legacy null sector caches because the old parser did not
            # unwrap nested F10 payloads. A parsed cache carries parser_version=2.
            if isinstance(val, dict) and int(val.get("parser_version", 0) or 0) >= 2:
                self._sector_mem[symbol] = val
                return val
        val: dict[str, Any] = {"name": None, "code": None, "source": "unknown"}
        if self.mcp is not None:
            try:
                raw = self.mcp.invoke("mcp_intel_tdx_f10", symbol=symbol, module="basic")
                name, code, diag = _extract_sector_fields(raw)
                val["name"] = name
                val["code"] = code
                val["source"] = "current_f10"
                val["parser_version"] = 2
                if not code:
                    val["diagnostic"] = diag
                    self.warnings.append(f"SECTOR_CODE_UNPARSED:{symbol}")
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
