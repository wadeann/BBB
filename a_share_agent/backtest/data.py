from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..mcp.base import MCPInvoker
from ..utils import stable_hash


def _date_str(v: Any) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    s = s[:10].replace("/", "-")
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        return s


def _as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


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
                    try:
                        return float(x[k])
                    except Exception:
                        pass
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
    point_in_time: bool = False
    membership_records: int = 0
    dynamic_daily: bool = False
    dataset_version: str | None = None
    coverage: float | None = None


@dataclass
class DailyUniverseSnapshot:
    date: str
    records: list[dict[str, Any]]
    source: str
    point_in_time: bool
    total: int
    dataset_version: str | None = None
    coverage: float | None = None
    warnings: list[str] | None = None

    @property
    def symbols(self) -> list[str]:
        return [str(x["symbol"]) for x in self.records if x.get("symbol")]

    @property
    def universe_hash(self) -> str:
        rows = [
            {
                "symbol": x.get("symbol"),
                "tradable": x.get("tradable"),
                "st": x.get("st"),
                "suspended": x.get("suspended"),
                "delisting_period": x.get("delisting_period"),
                "industry_code": x.get("industry_code"),
            }
            for x in self.records
        ]
        return stable_hash(rows)


class HistoricalDataProvider:
    """Point-in-time historical data provider for research/backtest.

    Preferred data path:
      1) local ``security_master.csv`` with effective intervals, or
      2) Intel ``mcp_intel_get_historical_universe`` interval export, or
      3) Intel daily point-in-time universe pages.

    Current-day wencai/F10 fallbacks are explicitly diagnostic only and are never
    silently promoted to research-grade data.
    """

    def __init__(self, root: Path, mcp: MCPInvoker | None = None, *, use_cache: bool = True):
        self.root = Path(root)
        self.mcp = mcp
        self.use_cache = use_cache
        self.cache_root = self.root / "data" / "backtest" / "cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._bars_mem: dict[str, list[dict[str, Any]]] = {}
        self._sector_mem: dict[str, dict[str, Any]] = {}
        self._sector_on_mem: dict[tuple[str, str], dict[str, Any]] = {}
        self._sector_intervals: dict[str, list[dict[str, Any]]] = {}
        self._daily_universe_mem: dict[str, DailyUniverseSnapshot] = {}
        self._daily_record_mem: dict[tuple[str, str], dict[str, Any]] = {}
        self.warnings: list[str] = []
        self._membership: dict[str, list[dict[str, Any]]] = {}
        self._period_universe: UniverseInfo | None = None
        self._daily_point_in_time_enabled = False
        self._universe_mode = "prefer_point_in_time"
        self._max_universe = 0

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

    @staticmethod
    def _unwrap_list(raw: Any) -> list[Any]:
        if isinstance(raw, dict):
            for key in ("data", "items", "symbols", "securities", "memberships", "result", "list"):
                if isinstance(raw.get(key), list):
                    return raw[key]
        return raw if isinstance(raw, list) else []

    def _normalize_membership_items(self, raw: Any) -> list[dict[str, Any]]:
        items = self._unwrap_list(raw)
        out: list[dict[str, Any]] = []
        for x in items:
            if isinstance(x, str):
                out.append({
                    "symbol": x,
                    "active_from": "",
                    "active_to": "",
                    "tradable": True,
                    "st": False,
                    "suspended": False,
                    "delisting_period": False,
                })
                continue
            if not isinstance(x, dict):
                continue
            symbol = x.get("symbol") or x.get("code") or x.get("证券代码") or x.get("股票代码")
            if not symbol:
                continue
            start = _date_str(x.get("active_from") or x.get("effective_from") or x.get("listing_date") or x.get("start_date"))
            end = _date_str(x.get("active_to") or x.get("effective_to") or x.get("delisting_date") or x.get("end_date"))
            industry_code = x.get("industry_code") or x.get("sector_code") or x.get("hy_code") or x.get("行业代码") or x.get("板块代码")
            industry_name = x.get("industry_name") or x.get("industry") or x.get("sector") or x.get("行业名称") or x.get("所属行业") or x.get("板块名称")
            listed = _as_bool(x.get("listed"), True)
            delisting_period = _as_bool(x.get("delisting_period") or x.get("delisting") or x.get("退市整理"), False)
            risk_warning = _as_bool(x.get("risk_warning") if x.get("risk_warning") is not None else x.get("st"), False)
            suspended = _as_bool(x.get("suspended") or x.get("停牌"), False)
            tradable_default = listed and not delisting_period and not risk_warning and not suspended
            out.append({
                "symbol": str(symbol),
                "name": x.get("name") or x.get("证券简称") or x.get("股票简称"),
                "active_from": start,
                "active_to": end,
                "listing_date": _date_str(x.get("listing_date")),
                "delisting_date": _date_str(x.get("delisting_date")),
                "listed": listed,
                "tradable": _as_bool(x.get("tradable"), tradable_default),
                "st": risk_warning,
                "risk_warning": risk_warning,
                "suspended": suspended,
                "delisting_period": delisting_period,
                "board": x.get("board") or x.get("market_board") or x.get("板块"),
                "industry_code": str(industry_code) if industry_code not in (None, "") else None,
                "industry_name": str(industry_name) if industry_name not in (None, "") else None,
                "available_at": x.get("available_at"),
                "raw": x,
            })
        return out

    @staticmethod
    def _record_eligible(rec: dict[str, Any], as_of: str) -> bool:
        start = str(rec.get("active_from") or rec.get("listing_date") or "")
        end = str(rec.get("active_to") or rec.get("delisting_date") or "")
        if start and as_of < start:
            return False
        if end and as_of > end:
            return False
        if not bool(rec.get("listed", True)):
            return False
        if not bool(rec.get("tradable", True)):
            return False
        if bool(rec.get("st", False)) or bool(rec.get("risk_warning", False)):
            return False
        if bool(rec.get("suspended", False)) or bool(rec.get("delisting_period", False)):
            return False
        return True

    def _register_memberships(self, items: list[dict[str, Any]]) -> None:
        for rec in items:
            sym = str(rec.get("symbol") or "")
            if not sym:
                continue
            bucket = self._membership.setdefault(sym, [])
            key = (rec.get("active_from"), rec.get("active_to"), rec.get("st"), rec.get("suspended"), rec.get("industry_code"))
            if not any((x.get("active_from"), x.get("active_to"), x.get("st"), x.get("suspended"), x.get("industry_code")) == key for x in bucket):
                bucket.append(rec)

    def _load_local_security_master(self, start_date: str, end_date: str) -> UniverseInfo | None:
        path = self.root / "data" / "backtest" / "security_master.csv"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            items = self._normalize_membership_items(list(csv.DictReader(fh)))
        if not items:
            return None
        self._register_memberships(items)
        syms = list(dict.fromkeys(str(x["symbol"]) for x in items))
        return UniverseInfo(
            syms,
            f"security_master:{path}",
            False,
            ["Local point-in-time security master used."],
            True,
            len(items),
            dynamic_daily=True,
        )

    def _parse_daily_universe_response(self, raw: Any, date: str, *, source: str) -> DailyUniverseSnapshot:
        meta = raw if isinstance(raw, dict) else {}
        records = self._normalize_membership_items(raw)
        dq = meta.get("data_quality") if isinstance(meta.get("data_quality"), dict) else {}
        point_in_time = bool(meta.get("point_in_time", dq.get("point_in_time", False)))
        total = int(meta.get("total") or len(records))
        coverage = dq.get("coverage", meta.get("coverage"))
        try:
            coverage = float(coverage) if coverage is not None else None
        except Exception:
            coverage = None
        return DailyUniverseSnapshot(
            date=date,
            records=records,
            source=str(meta.get("source") or source),
            point_in_time=point_in_time,
            total=total,
            dataset_version=str(meta.get("dataset_version")) if meta.get("dataset_version") is not None else None,
            coverage=coverage,
            warnings=list(dq.get("warnings") or []),
        )

    def _fetch_daily_universe(self, date: str) -> DailyUniverseSnapshot:
        if date in self._daily_universe_mem:
            return self._daily_universe_mem[date]
        path = self._cache_file("daily_universe", date)
        if self.use_cache and path.exists():
            cached = self._load_json(path)
            if isinstance(cached, dict):
                snap = self._parse_daily_universe_response(cached, date, source="cache")
                if snap.records:
                    self._daily_universe_mem[date] = snap
                    for rec in snap.records:
                        self._daily_record_mem[(date, str(rec["symbol"]))] = rec
                    return snap
        if self.mcp is None:
            raise RuntimeError("historical daily universe MCP unavailable")

        # Contract supports stable pagination. Some servers ignore page/limit and return all rows.
        page = 1
        page_size = 500
        records: list[dict[str, Any]] = []
        total: int | None = None
        first_meta: dict[str, Any] | None = None
        while page <= 50:
            raw = self.mcp.invoke(
                "mcp_intel_get_historical_universe",
                date=date,
                market="A_SHARE",
                include_suspended=True,
                include_risk_warning=True,
                include_delisting=True,
                page=page,
                limit=page_size,
            )
            if first_meta is None and isinstance(raw, dict):
                first_meta = raw
            part = self._normalize_membership_items(raw)
            if isinstance(raw, dict) and raw.get("total") is not None:
                try:
                    total = int(raw.get("total"))
                except Exception:
                    pass
            if not part:
                break
            records.extend(part)
            if total is not None and len(records) >= total:
                break
            # If the server advertises total, continue even when it internally caps
            # page size below our requested limit. Without total, a short page ends it.
            if total is None and len(part) < page_size:
                break
            page += 1
        meta = first_meta or {}
        meta = dict(meta)
        meta["data"] = records
        if total is not None:
            meta["total"] = total
        snap = self._parse_daily_universe_response(meta, date, source="mcp_historical_universe_daily")
        # Stable unique symbol set; duplicate pages are a data error, not an excuse to overcount.
        dedup = {str(x["symbol"]): x for x in snap.records}
        snap.records = [dedup[k] for k in sorted(dedup)]
        if total is not None and len(snap.records) != total:
            self.warnings.append(f"PARTIAL_UNIVERSE:{date}:got={len(snap.records)}:total={total}")
        if self._universe_mode == "strict_point_in_time" and not snap.point_in_time:
            raise RuntimeError(f"historical universe for {date} is not point-in-time")
        if self._universe_mode == "strict_point_in_time" and total is not None and len(snap.records) != total:
            raise RuntimeError(f"partial historical universe for {date}: {len(snap.records)}/{total}")
        if not snap.records:
            raise RuntimeError(f"empty historical universe for {date}")
        self._daily_universe_mem[date] = snap
        for rec in snap.records:
            self._daily_record_mem[(date, str(rec["symbol"]))] = rec
        if self.use_cache:
            self._save_json(path, {
                "trade_date": date,
                "point_in_time": snap.point_in_time,
                "source": snap.source,
                "total": snap.total,
                "dataset_version": snap.dataset_version,
                "data_quality": {"point_in_time": snap.point_in_time, "coverage": snap.coverage, "warnings": snap.warnings or []},
                "data": snap.records,
            })
        return snap

    def load_universe_for_period(self, start_date: str, end_date: str, universe_file: str, *, max_universe: int = 0, mode: str = "prefer_point_in_time") -> UniverseInfo:
        self._universe_mode = mode
        self._max_universe = max(0, int(max_universe or 0))
        if mode in {"strict_point_in_time", "prefer_point_in_time"}:
            local = self._load_local_security_master(start_date, end_date)
            if local:
                if self._max_universe:
                    local.symbols = local.symbols[: self._max_universe]
                self._period_universe = local
                return local
            if self.mcp is not None:
                # Fast path: interval export for the whole period. This is preferred for
                # large full-market backtests because it avoids ~500 daily universe calls.
                try:
                    raw = self.mcp.invoke(
                        "mcp_intel_get_historical_universe",
                        start_date=start_date,
                        end_date=end_date,
                        mode="membership_intervals",
                        include_status=True,
                    )
                    items = self._normalize_membership_items(raw)
                    interval_items = [x for x in items if x.get("active_from") or x.get("active_to")]
                    point_in_time = bool(raw.get("point_in_time", True)) if isinstance(raw, dict) else bool(interval_items)
                    if items and interval_items and point_in_time:
                        self._register_memberships(items)
                        syms = list(dict.fromkeys(str(x["symbol"]) for x in items))
                        if self._max_universe:
                            syms = syms[: self._max_universe]
                        info = UniverseInfo(
                            syms,
                            "mcp_historical_universe_intervals",
                            False,
                            ["Point-in-time membership intervals returned by Intel MCP."],
                            True,
                            len(items),
                            dynamic_daily=True,
                            dataset_version=str(raw.get("dataset_version")) if isinstance(raw, dict) and raw.get("dataset_version") is not None else None,
                            coverage=float((raw.get("data_quality") or {}).get("coverage")) if isinstance(raw, dict) and isinstance(raw.get("data_quality"), dict) and (raw.get("data_quality") or {}).get("coverage") is not None else None,
                        )
                        self._period_universe = info
                        return info
                    if items:
                        self.warnings.append("HISTORICAL_UNIVERSE_NO_VALID_INTERVALS")
                except Exception as exc:
                    self.warnings.append(f"HISTORICAL_INTERVAL_UNIVERSE_UNAVAILABLE:{type(exc).__name__}")

                # Daily point-in-time path. Probe one date now; remaining dates are
                # fetched lazily and cached by the engine.
                try:
                    # start_date may be a weekend/holiday. Probe forward until the first
                    # historical trading-date snapshot is available.
                    probe_date = datetime.fromisoformat(start_date).date()
                    snap = None
                    last_exc: Exception | None = None
                    for offset in range(15):
                        probe = (probe_date + timedelta(days=offset)).isoformat()
                        try:
                            snap = self._fetch_daily_universe(probe)
                            if snap.records:
                                break
                        except Exception as exc:
                            last_exc = exc
                            continue
                    if snap is None or not snap.records:
                        raise last_exc or RuntimeError("no historical daily universe near start date")
                    if snap.point_in_time:
                        self._daily_point_in_time_enabled = True
                        syms = snap.symbols[: self._max_universe or None]
                        info = UniverseInfo(
                            syms,
                            "mcp_historical_universe_daily",
                            False,
                            ["Daily point-in-time universe will be reconstructed for every backtest trading day."],
                            True,
                            len(snap.records),
                            dynamic_daily=True,
                            dataset_version=snap.dataset_version,
                            coverage=snap.coverage,
                        )
                        self._period_universe = info
                        return info
                    self.warnings.append("HISTORICAL_DAILY_UNIVERSE_NOT_POINT_IN_TIME")
                except Exception as exc:
                    self.warnings.append(f"HISTORICAL_DAILY_UNIVERSE_UNAVAILABLE:{type(exc).__name__}")
            if mode == "strict_point_in_time":
                raise RuntimeError(
                    "strict point-in-time universe requested, but no local security_master.csv, interval export, or daily historical universe is available"
                )

        info = self.load_universe(universe_file, max_universe=self._max_universe)
        info.notes.append(f"universe_mode={mode}; point-in-time source unavailable, fallback used")
        self._period_universe = info
        return info

    @property
    def dynamic_universe_enabled(self) -> bool:
        return bool(self._daily_point_in_time_enabled or self._membership or (self._period_universe and self._period_universe.dynamic_daily))

    @property
    def point_in_time_universe_enabled(self) -> bool:
        return bool(self._period_universe and self._period_universe.point_in_time)

    def active_records_on(self, as_of: str, fallback_symbols: list[str] | None = None) -> list[dict[str, Any]]:
        if self._daily_point_in_time_enabled:
            snap = self._fetch_daily_universe(as_of)
            records = [x for x in snap.records if self._record_eligible(x, as_of)]
            if self._max_universe:
                records = records[: self._max_universe]
            return records
        if self._membership:
            rows: list[dict[str, Any]] = []
            allowed = set(fallback_symbols or []) if fallback_symbols else None
            for sym in sorted(self._membership):
                if allowed is not None and sym not in allowed:
                    continue
                chosen = None
                for rec in self._membership[sym]:
                    start = str(rec.get("active_from") or rec.get("listing_date") or "")
                    end = str(rec.get("active_to") or rec.get("delisting_date") or "")
                    if start and as_of < start:
                        continue
                    if end and as_of > end:
                        continue
                    chosen = rec
                    if self._record_eligible(rec, as_of):
                        rows.append(rec)
                        break
                if chosen is not None:
                    self._daily_record_mem[(as_of, sym)] = chosen
            if self._max_universe:
                rows = rows[: self._max_universe]
            return rows
        return [{"symbol": s, "tradable": True} for s in (fallback_symbols or []) if self.eligible_on(s, as_of)]

    def active_symbols_on(self, as_of: str, fallback_symbols: list[str] | None = None) -> list[str]:
        return [str(x["symbol"]) for x in self.active_records_on(as_of, fallback_symbols)]

    def daily_universe_meta(self, as_of: str, fallback_symbols: list[str] | None = None) -> dict[str, Any]:
        records = self.active_records_on(as_of, fallback_symbols)
        if as_of in self._daily_universe_mem:
            snap = self._daily_universe_mem[as_of]
            source = snap.source
            pit = snap.point_in_time
            dataset_version = snap.dataset_version
            coverage = snap.coverage
            universe_hash = snap.universe_hash
        else:
            source = self._period_universe.source if self._period_universe else "fallback"
            pit = bool(self._period_universe.point_in_time) if self._period_universe else False
            dataset_version = self._period_universe.dataset_version if self._period_universe else None
            coverage = self._period_universe.coverage if self._period_universe else None
            universe_hash = stable_hash(sorted(str(x["symbol"]) for x in records))
        return {
            "date": as_of,
            "active_symbols": len(records),
            "source": source,
            "point_in_time": pit,
            "dataset_version": dataset_version,
            "coverage": coverage,
            "universe_hash": universe_hash,
        }

    def eligible_on(self, symbol: str, as_of: str) -> bool:
        rec = self._daily_record_mem.get((as_of, symbol))
        if rec is not None:
            return self._record_eligible(rec, as_of)
        records = self._membership.get(symbol)
        if not records:
            return True
        return any(self._record_eligible(r, as_of) for r in records)

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
            target = max_universe or 5000
            page = 1
            page_size = min(50, target)
            while len(syms) < target and page <= 120:
                raw = self.mcp.invoke("mcp_intel_wencai_search", query="A股 非ST 非退市 非停牌 上市超过20个交易日", page=page, limit=page_size)
                items = raw.get("data", raw) if isinstance(raw, dict) else raw
                if not isinstance(items, list) or not items:
                    break
                before = len(syms)
                for x in items:
                    if isinstance(x, str):
                        syms.append(x)
                    elif isinstance(x, dict) and (x.get("symbol") or x.get("code")):
                        syms.append(str(x.get("symbol") or x.get("code")))
                syms = list(dict.fromkeys(syms))
                if len(items) < page_size or len(syms) == before:
                    break
                page += 1
            source = "mcp_current_universe"
            survivorship = True
            notes = ["Current-stock universe used for historical test: survivorship bias is possible.", f"Paged wencai universe retrieval; requested cap={target}."]
        else:
            source = "none"
            survivorship = True
            notes = ["No universe source available."]
        syms = list(dict.fromkeys(syms))
        if max_universe and max_universe > 0:
            syms = syms[:max_universe]
        return UniverseInfo(syms, source, survivorship, notes, point_in_time=False, dynamic_daily=False)

    def bars(self, symbol: str, *, count: int = 900) -> list[dict[str, Any]]:
        if symbol in self._bars_mem:
            return self._bars_mem[symbol]
        path = self._cache_file("bars", symbol)
        if self.use_cache and path.exists():
            rows = normalize_bars(self._load_json(path))
            if rows:
                self._bars_mem[symbol] = rows
                return rows
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

    @staticmethod
    def _recursive_dicts(raw: Any, *, max_nodes: int = 200) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        stack = [raw]
        while stack and len(out) < max_nodes:
            cur = stack.pop()
            if isinstance(cur, dict):
                out.append(cur)
                stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
            elif isinstance(cur, list):
                stack.extend(cur[:100])
        return out

    @classmethod
    def _parse_sector_fields(cls, raw: Any) -> dict[str, Any]:
        name_keys = ("industry_name", "industry", "sector", "hy_name", "所属行业", "行业", "行业名称", "板块名称")
        code_keys = ("industry_code", "sector_code", "hy_code", "行业代码", "板块代码")
        for node in cls._recursive_dicts(raw):
            name = next((node.get(k) for k in name_keys if node.get(k) not in (None, "")), None)
            code = next((node.get(k) for k in code_keys if node.get(k) not in (None, "")), None)
            if name is not None or code is not None:
                return {"name": str(name) if name is not None else None, "code": str(code) if code is not None else None}
        return {"name": None, "code": None}

    def sector_info(self, symbol: str) -> dict[str, Any]:
        if symbol in self._sector_mem:
            return self._sector_mem[symbol]
        path = self._cache_file("sector_map", symbol)
        if self.use_cache and path.exists():
            val = self._load_json(path)
            if isinstance(val, dict) and (val.get("name") or val.get("code")):
                self._sector_mem[symbol] = val
                return val
        val: dict[str, Any] = {"name": None, "code": None, "source": "unknown"}
        if self.mcp is not None:
            try:
                raw = self.mcp.invoke("mcp_intel_tdx_f10", symbol=symbol, module="basic")
                parsed = self._parse_sector_fields(raw)
                val.update(parsed)
                val["source"] = "current_f10"
                if not (val.get("name") or val.get("code")):
                    keys = sorted({str(k) for n in self._recursive_dicts(raw, max_nodes=50) for k in n.keys()})[:80]
                    val["diagnostic_keys"] = keys
            except Exception as exc:
                self.warnings.append(f"SECTOR_LOOKUP_FAILED:{symbol}:{type(exc).__name__}")
        self._sector_mem[symbol] = val
        if self.use_cache:
            self._save_json(path, val)
        return val

    def sector_info_on(self, symbol: str, as_of: str, *, strict: bool = False) -> dict[str, Any]:
        key = (as_of, symbol)
        if key in self._sector_on_mem:
            return self._sector_on_mem[key]
        rec = self._daily_record_mem.get(key)
        if rec and (rec.get("industry_code") or rec.get("industry_name")):
            val = {"name": rec.get("industry_name"), "code": rec.get("industry_code"), "source": "historical_universe"}
            self._sector_on_mem[key] = val
            return val
        # Interval security master may contain sector fields.
        for r in self._membership.get(symbol, []):
            start = str(r.get("active_from") or r.get("listing_date") or "")
            end = str(r.get("active_to") or r.get("delisting_date") or "")
            if start and as_of < start:
                continue
            if end and as_of > end:
                continue
            if r.get("industry_code") or r.get("industry_name"):
                val = {"name": r.get("industry_name"), "code": r.get("industry_code"), "source": "historical_security_master"}
                self._sector_on_mem[key] = val
                return val
        if self.mcp is not None:
            # Reuse effective-date intervals whenever the server supplies them. This
            # avoids N_symbols × N_days historical sector MCP calls in full-market runs.
            for item in self._sector_intervals.get(symbol, []):
                start = _date_str(item.get("effective_from") or item.get("active_from"))
                end = _date_str(item.get("effective_to") or item.get("active_to"))
                if start and as_of < start:
                    continue
                if end and as_of > end:
                    continue
                if str(item.get("type", "industry")).lower() == "industry":
                    val = {"name": item.get("name"), "code": item.get("code"), "source": "historical_sector_membership"}
                    self._sector_on_mem[key] = val
                    return val

            interval_path = self._cache_file("historical_sector_intervals", symbol)
            dated_path = self._cache_file("historical_sector_daily", f"{as_of}_{symbol}")
            raw = None
            if self.use_cache and interval_path.exists():
                candidate = self._load_json(interval_path)
                items = [x for x in self._unwrap_list(candidate) if isinstance(x, dict)]
                if items and any(x.get("effective_from") or x.get("effective_to") or x.get("active_from") or x.get("active_to") for x in items):
                    raw = candidate
            if raw is None and self.use_cache and dated_path.exists():
                raw = self._load_json(dated_path)
            if raw is None:
                try:
                    raw = self.mcp.invoke("mcp_intel_get_historical_sector_membership", symbol=symbol, as_of=as_of)
                except Exception as exc:
                    self.warnings.append(f"HISTORICAL_SECTOR_UNAVAILABLE:{symbol}:{as_of}:{type(exc).__name__}")
                    raw = None

            if raw is not None:
                memberships = [x for x in self._unwrap_list(raw) if isinstance(x, dict)]
                has_intervals = bool(memberships) and any(
                    x.get("effective_from") or x.get("effective_to") or x.get("active_from") or x.get("active_to")
                    for x in memberships
                )
                if has_intervals:
                    self._sector_intervals[symbol] = memberships
                    if self.use_cache:
                        self._save_json(interval_path, raw)
                elif self.use_cache:
                    self._save_json(dated_path, raw)
                industry = next((x for x in memberships if str(x.get("type", "industry")).lower() == "industry"
                                 and (not _date_str(x.get("effective_from") or x.get("active_from")) or as_of >= _date_str(x.get("effective_from") or x.get("active_from")))
                                 and (not _date_str(x.get("effective_to") or x.get("active_to")) or as_of <= _date_str(x.get("effective_to") or x.get("active_to")))), None)
                if industry:
                    val = {"name": industry.get("name"), "code": industry.get("code"), "source": "historical_sector_membership"}
                    self._sector_on_mem[key] = val
                    return val
        if strict:
            val = {"name": None, "code": None, "source": "missing_historical_sector"}
        else:
            val = self.sector_info(symbol)
        self._sector_on_mem[key] = val
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
                self._bars_mem[key] = rows
                return rows
        rows: list[dict[str, Any]] = []
        if self.mcp is not None:
            try:
                rows = normalize_bars(self.mcp.invoke("mcp_intel_fetch_sector_history", code=code))
            except Exception as exc:
                self.warnings.append(f"SECTOR_BARS_FAILED:{code}:{type(exc).__name__}")
        self._bars_mem[key] = rows
        if rows and self.use_cache:
            self._save_json(path, rows)
        return rows
