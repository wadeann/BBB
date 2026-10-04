from __future__ import annotations

import csv
import json
import hashlib
import io
import os
import tempfile
from functools import wraps
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..mcp.base import MCPInvoker
from ..utils import stable_hash
from .corporate_actions import CorporateAction, CorporateActionEngine


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
        for key in ("data", "bars", "klines", "items", "result", "list", "Rows", "rows"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for x in raw:
        if not isinstance(x, dict):
            continue
        d = _date_str(x.get("date") or x.get("trade_date") or x.get("time") or x.get("datetime") or x.get("Data"))
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
            "open": f("open", "o", "Open"),
            "high": f("high", "h", "High"),
            "low": f("low", "l", "Low"),
            "close": f("close", "c", "Close"),
            "volume": f("volume", "vol", "v", "Volume", "RawVolume"),
            "amount": f("amount", "turnover", "Amount", "RawAmount", default=0.0),
            "pct": f("pct", "pct_chg", "change_pct", "ChangePct", default=0.0),
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


class ConsumedInputLedger:
    """Append-only commitments to the bytes parsed by one walk-forward run."""

    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        self.fold_id = None
        self.phase = "initialization"
        self.requested_range = None
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    def checked_path(self, path: Path) -> Path:
        path = Path(path).absolute()
        if (any(p.is_symlink() for p in (path, *path.parents))
                or not path.resolve().is_relative_to(self.root.resolve())):
            raise ValueError("consumed input path escape/symlink rejected")
        return path

    def append(self, **fields: Any) -> dict[str, Any]:
        event = dict(sequence=len(self.events) + 1, fold_id=self.fold_id,
            phase=self.phase, kind="unknown", logical_key=None, requested_range=None,
            returned={"row_count": 0, "date_start": None, "date_end": None},
            source_type="memory", path=None, sha256=None, response_json_sha256=None,
            wire_sha256=None, wire_byte_count=None, wire_status_code=None,
            wire_content_type=None, wire_transport=None, wire_artifact_path=None,
            wire_artifact_root=str(self.root), dataset_version=None,
            dataset_metadata={}, cache_status="not_applicable", status="ok",
            origin_sequences=[], verifiable=False)
        event.update(fields)
        self.events.append(event)
        return event

    @staticmethod
    def returned(value: Any) -> dict[str, Any]:
        rows = value if isinstance(value, list) else (value.get("data", []) if isinstance(value, dict) else
            [{"symbol": s} for s in value.symbols] if isinstance(value, UniverseInfo) else [])
        if isinstance(value, dict) and isinstance(value.get('bars'), dict):
            rows = [row for symbol_rows in value['bars'].values()
                    if isinstance(symbol_rows, list) for row in symbol_rows]
        dates = sorted(str(r.get("date") or r.get("ex_date") or r.get("effective_from") or "")
            for r in rows if isinstance(r, dict))
        dates = [d for d in dates if d]
        return {"row_count": len(rows), "date_start": dates[0] if dates else None,
                "date_end": dates[-1] if dates else None}

    def read(self, path: Path, *, kind: str, parser: Any = None) -> Any:
        path = Path(path).absolute()
        cache = path.is_relative_to(self.root / "data/backtest/cache")
        fields = dict(kind=kind, logical_key=str(path), path=str(path),
            source_type="cache" if cache else "physical",
            cache_status="hit" if cache else "not_applicable", requested_range=self.requested_range)
        raw = None
        try:
            path = self.checked_path(path)
            raw = path.read_bytes()
            value = parser(raw) if parser else raw
        except Exception as exc:
            self.append(**fields, status="missing" if isinstance(exc, FileNotFoundError) else "error",
                        sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None,
                        byte_count=len(raw) if raw is not None else None,
                        error=f"{type(exc).__name__}: {exc}")
            raise
        metadata = {k: value[k] for k in ("dataset_version", "source", "source_type")
                    if isinstance(value, dict) and k in value}
        self.append(**fields, sha256=hashlib.sha256(raw).hexdigest(),
            byte_count=len(raw), returned=self.returned(value), dataset_metadata=metadata,
            dataset_version=metadata.get("dataset_version"), verifiable=not cache,
            authenticity_reason="cache origin is not byte-bound" if cache else None,
            status="empty" if not value else "ok")
        return value

    @staticmethod
    def physical_inputs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"sequence": e["sequence"], "fold_id": e["fold_id"], "kind": e["kind"], "path": e["path"], "sha256": e["sha256"]} for e in events if e["source_type"] in {"physical", "cache"} and e.get("sha256")]

    def publish_wire(self, envelope: Any) -> dict[str, Any]:
        from ..mcp.http import MCPResponseEnvelope
        if not isinstance(envelope, MCPResponseEnvelope):
            return {}
        directory = self.checked_path(self.root / 'data/backtest/mcp_wire')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory = self.checked_path(directory)
        directory.chmod(0o700)
        def publish(raw: bytes, suffix: str) -> Path:
            fd, temporary = tempfile.mkstemp(dir=directory, prefix='.capture-')
            artifact = directory / f'{Path(temporary).name[1:]}-{hashlib.sha256(raw).hexdigest()}.{suffix}'
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                    os.fchmod(stream.fileno(), 0o400)
                os.link(temporary, artifact, follow_symlinks=False)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return artifact
        artifact = publish(envelope.wire_bytes, 'bin')
        capsule = dict(request_hex=envelope.request_bytes.hex(), wire_sha256=envelope.wire_sha256,
                       wire_byte_count=envelope.wire_byte_count, status_code=envelope.status_code,
                       content_type=envelope.content_type, transport=envelope.transport)
        capsule_bytes = json.dumps(capsule, sort_keys=True, separators=(',', ':')).encode('utf-8')
        capsule_path = publish(capsule_bytes, 'json')
        return dict(wire_sha256=envelope.wire_sha256, wire_byte_count=envelope.wire_byte_count,
                    wire_status_code=envelope.status_code, wire_content_type=envelope.content_type,
                    wire_transport=envelope.transport,
                    wire_artifact_path=str(artifact.relative_to(self.root)),
                    wire_capsule_path=str(capsule_path.relative_to(self.root)),
                    wire_capsule_sha256=hashlib.sha256(capsule_bytes).hexdigest(),
                    wire_request_json=envelope.request_bytes.decode('utf-8'))

    def verify_mcp_event(self, event: dict[str, Any]) -> bool:
        from ..mcp.http import _decode_sse, _unwrap_tool_result
        try:
            request = json.loads(event['request_json'])
            capsule_path = Path(event['wire_capsule_path'])
            capsule_path = self.checked_path(capsule_path if capsule_path.is_absolute() else self.root / capsule_path)
            capsule_bytes = capsule_path.read_bytes()
            if hashlib.sha256(capsule_bytes).hexdigest() != event.get('wire_capsule_sha256'):
                return False
            capsule = json.loads(capsule_bytes)
            request_bytes = bytes.fromhex(capsule['request_hex'])
            if request_bytes.decode('utf-8') != event['wire_request_json']:
                return False
            if any(capsule[key] != event[field] for key, field in (
                    ('wire_sha256', 'wire_sha256'), ('wire_byte_count', 'wire_byte_count'),
                    ('status_code', 'wire_status_code'), ('content_type', 'wire_content_type'),
                    ('transport', 'wire_transport'))):
                return False
            rpc_request = json.loads(request_bytes)
            response = json.loads(event['response_json'])
            metadata = event['dataset_metadata']
            quality = response.get('data_quality') or {}
            expected = {k: response.get(k, quality.get(k)) for k in ('dataset_version', 'source', 'point_in_time', 'coverage', 'coverage_start', 'coverage_end')}
            args = request['arguments']
            start, end = metadata.get('coverage_start'), metadata.get('coverage_end')
            requested_start = args.get('start_date') or args.get('date') or args.get('as_of')
            requested_end = args.get('end_date') or requested_start
            artifact = Path(event['wire_artifact_path'])
            artifact = self.checked_path(artifact if artifact.is_absolute() else self.root / artifact)
            wire = artifact.read_bytes()
            content_type = str(event.get('wire_content_type') or '').lower()
            if 'text/event-stream' in content_type:
                items = _decode_sse(wire.decode('utf-8'))
                rpc_response = next(item for item in reversed(items)
                    if isinstance(item, dict) and ('result' in item or 'error' in item or 'id' in item))
            elif 'application/json' in content_type:
                rpc_response = json.loads(wire)
            else:
                return False
            coverage = metadata.get('coverage')
            complete = type(coverage) in (int, float) and coverage == 1.0
            if isinstance(coverage, dict):
                symbols = args.get('symbols')
                symbol_coverage = coverage.get('symbols')
                bars = response.get('bars')
                returned_rows = response.get('returned_rows')
                complete = (isinstance(symbols, list) and bool(symbols)
                    and len(symbols) == len(set(symbols))
                    and coverage.get('status') == 'complete' and coverage.get('complete') is True
                    and coverage.get('requested_start') == requested_start
                    and coverage.get('requested_end') == requested_end
                    and isinstance(symbol_coverage, dict) and set(symbol_coverage) == set(symbols)
                    and isinstance(bars, dict) and set(bars) == set(symbols)
                    and isinstance(returned_rows, dict) and set(returned_rows) == set(symbols))
                if complete:
                    for symbol in symbols:
                        rows = bars[symbol]
                        detail = symbol_coverage[symbol]
                        if not isinstance(rows, list) or not rows or not isinstance(detail, dict):
                            complete = False
                            break
                        dates = sorted(_date_str(row.get('time') or row.get('date'))
                                       for row in rows if isinstance(row, dict))
                        if (len(dates) != len(rows) or not all(dates)
                                or detail.get('complete') is not True
                                or type(detail.get('rows')) is not int or detail['rows'] != len(rows)
                                or type(returned_rows[symbol]) is not int or returned_rows[symbol] != len(rows)
                                or detail.get('start') != dates[0] or detail.get('end') != dates[-1]
                                or not requested_start or not requested_end
                                or dates[0] < requested_start or dates[-1] > requested_end):
                            complete = False
                            break
            target = rpc_request['params']['name']
            return (
                event['service'] == 'intel'
                and request['tool'] == event['tool']
                and event['status'] == 'ok'
                and event['tool'].startswith('mcp_intel_')
                and rpc_request.get('jsonrpc') == '2.0'
                and rpc_request.get('method') == 'tools/call'
                and target in {event['tool'], event['tool'][len('mcp_intel_'):]}
                and self.digest(rpc_request['params']['arguments']) == self.digest(args)
                and type(rpc_request.get('id')) in (int, str)
                and rpc_response.get('jsonrpc') == '2.0'
                and type(rpc_response.get('id')) is type(rpc_request['id'])
                and rpc_response['id'] == rpc_request['id']
                and 'result' in rpc_response
                and self.digest(_unwrap_tool_result(rpc_response)) == self.digest(response)
                and response.get('status', 'ok') in {'ok', 'success'}
                and response.get('adjustment') != 'provider_declared'
                and event.get('response_json_sha256') == self.digest(response)
                and event['request_sha256'] == self.digest(request)
                and event.get('wire_sha256') == hashlib.sha256(wire).hexdigest()
                and event.get('wire_byte_count') == len(wire)
                and type(event.get('wire_status_code')) is int
                and 200 <= event['wire_status_code'] < 300
                and event.get('wire_transport') == 'streamable_http'
                and self.digest(metadata) == self.digest(expected)
                and bool(metadata.get('source'))
                and bool(metadata.get('dataset_version'))
                and metadata.get('point_in_time') is True
                and complete
                and isinstance(start, str) and isinstance(end, str) and start <= end
                and (not requested_start or start <= requested_start)
                and (not requested_end or end >= requested_end)
                and not event.get('redacted')
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, RuntimeError):
            return False
    def snapshot(self, *, verify_files: bool = False) -> dict[str, Any]:
        reasons = [f"event {e['sequence']} {e['kind']}: {e.get('authenticity_reason') or e['status']}"
                   for e in self.events if not e["verifiable"]]
        reasons.extend(f"event {e['sequence']}: MCP response commitment incomplete/mismatched"
                       for e in self.events if e['source_type'] == 'mcp' and not self.verify_mcp_event(e))
        if verify_files:
            for entry in self.physical_inputs(self.events):
                try:
                    path = self.checked_path(Path(entry["path"]))
                    if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                        raise ValueError("consumed bytes changed")
                except (OSError, ValueError) as exc:
                    reasons.append(f"event {entry['sequence']}: {exc}")
        events = json.loads(json.dumps(self.events))
        return dict(events=events, sha256=self.digest(events),
            status="VERIFIED" if events and not reasons else "UNVERIFIABLE",
            reasons=reasons, physical_inputs=self.physical_inputs(events), root=str(self.root))


def _record_request(method):
    @wraps(method)
    def recorded(self, *args, **kwargs):
        ledger = self.ledger
        start = len(ledger.events)
        key = (method.__name__, repr(args)) if method.__name__ in {"bars", "raw_bars", "sector_bars"} else (method.__name__, repr(args), repr(sorted(kwargs.items())))
        prior_request = ledger.requested_range
        ledger.requested_range = {'args': list(args), 'kwargs': kwargs}
        try:
            result = method(self, *args, **kwargs)
        except Exception as exc:
            ledger.append(kind=method.__name__, logical_key=repr(args), requested_range=kwargs,
                          status="error", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            ledger.requested_range = prior_request
        origins = sorted({n for e in ledger.events[start:]
            for n in ([e['sequence']] if e['source_type'] in {'physical', 'cache', 'mcp'}
                      else e.get('origin_sequences', []))})
        if not origins:
            origins = self._request_origins.get(key, [])
        if not origins and method.__name__ in {"active_records_on", "sector_info_on"}:
            kinds = ({"security_master", "historical_status_intervals"} if method.__name__ == "active_records_on"
                     else {"security_master"} if result.get('source') == 'historical_security_master'
                     else {"historical_sector_intervals"} if result.get('source') == 'historical_sector_interval'
                     else set())
            origins = [e['sequence'] for e in ledger.events if e['fold_id'] == ledger.fold_id
                and e['source_type'] in {'physical', 'cache'} and e['kind'] in kinds]
        self._request_origins[key] = origins
        empty = (result is None or result == [] or
                 isinstance(result, UniverseInfo) and not result.symbols or
                 isinstance(result, dict) and result.get("source") in {"unknown", "missing_historical_sector"})
        good = bool(origins) and all(ledger.events[n - 1]["verifiable"] for n in origins)
        ledger.append(kind=method.__name__, logical_key=repr(args), requested_range={'args': list(args), 'kwargs': kwargs},
            returned=ledger.returned(result), source_type="memory", origin_sequences=origins,
            cache_status="hit" if len(ledger.events) == start else "miss",
            status="empty" if empty else "ok", verifiable=good and not empty)
        return result
    return recorded


class _ProvenanceMCP:
    """Record transport-independent JSON commitments, never endpoint/auth state."""

    HISTORICAL_TOOLS = frozenset({'mcp_intel_get_historical_universe',
        'mcp_intel_get_historical_security', 'mcp_intel_get_historical_sector_membership',
        'mcp_intel_tdx_kline', 'mcp_intel_fetch_kline', 'mcp_intel_fetch_sector_history',
        'mcp_intel_historical_bars'})

    def __init__(self, invoker: MCPInvoker, ledger: ConsumedInputLedger, read_only: bool):
        self.invoker, self.ledger, self.read_only = invoker, ledger, read_only

    @staticmethod
    def _safe_wire(envelope: Any) -> bool:
        from ..mcp.http import MCPResponseEnvelope, _decode_sse
        from ..utils import redact
        if not isinstance(envelope, MCPResponseEnvelope):
            return False
        try:
            content_type = envelope.content_type.lower()
            if 'text/event-stream' in content_type:
                objects = _decode_sse(envelope.wire_bytes.decode('utf-8'))
                if not objects or any(not isinstance(obj, dict) for obj in objects):
                    return False
            elif 'application/json' in content_type:
                objects = [json.loads(envelope.wire_bytes)]
            else:
                return False
            objects.append(json.loads(envelope.request_bytes))
            return all(ConsumedInputLedger.digest(obj) == ConsumedInputLedger.digest(
                redact(obj, never_log={'username', 'token', 'credentials'})) for obj in objects)
        except (ValueError, TypeError, AttributeError):
            return False

    def invoke(self, tool_name: str, **kwargs: Any) -> Any:
        from ..utils import redact
        request = redact({'tool': tool_name, 'arguments': kwargs}, never_log={'username', 'token', 'credentials'})
        request_json = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        fields = dict(kind='mcp_call', logical_key=tool_name, source_type='mcp',
            tool=tool_name, service='intel' if tool_name.startswith('mcp_intel_') else 'unknown',
            requested_range=request['arguments'], request_json=request_json,
            request_sha256=ConsumedInputLedger.digest(request))
        if self.read_only and tool_name not in self.HISTORICAL_TOOLS:
            self.ledger.append(**fields, status='denied', error='non-historical tool denied')
            raise RuntimeError('read-only historical MCP tool denied')
        envelope = None
        try:
            evidence_call = getattr(self.invoker, 'invoke_with_evidence', None)
            if callable(evidence_call):
                response, envelope = evidence_call(tool_name, **kwargs)
            else:
                response = self.invoker.invoke(tool_name, **kwargs)
            response_json = json.dumps(response, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            safe_response = redact(response, never_log={'username', 'token', 'credentials'})
            safe_response_json = json.dumps(safe_response, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        except Exception as exc:
            error_envelope = getattr(exc, 'response_envelope', None)
            sensitive = request['arguments'] != kwargs or (error_envelope is not None and not self._safe_wire(error_envelope))
            wire_fields = self.ledger.publish_wire(error_envelope) if not sensitive else {}
            self.ledger.append(**fields, **wire_fields, redacted=sensitive, status='error', error=type(exc).__name__)
            raise RuntimeError(f'historical MCP call failed: {type(exc).__name__}') from None
        quality = (safe_response.get('data_quality') or {}) if isinstance(safe_response, dict) else {}
        metadata = {k: safe_response.get(k, quality.get(k)) for k in
                    ('dataset_version', 'source', 'point_in_time', 'coverage', 'coverage_start', 'coverage_end')} if isinstance(safe_response, dict) else {}
        rows = response.get('data', response.get('memberships', response)) if isinstance(response, dict) else response
        redacted = (safe_response_json != response_json or request['arguments'] != kwargs
                    or envelope is not None and not self._safe_wire(envelope))
        wire_fields = self.ledger.publish_wire(envelope) if not redacted else {}
        event = self.ledger.append(**fields, response_json=safe_response_json,
            redacted=redacted,
            sha256=hashlib.sha256(response_json.encode()).hexdigest(),
            response_json_sha256=ConsumedInputLedger.digest(safe_response),
            byte_count=len(response_json.encode()), **wire_fields,
            dataset_metadata=metadata, dataset_version=metadata.get('dataset_version'),
            returned=self.ledger.returned(response), status='empty' if not rows else 'ok')
        if tool_name == 'mcp_intel_historical_bars' and isinstance(safe_response, dict):
            event['response_hash'] = safe_response.get('response_hash')
            event['adjustment'] = safe_response.get('adjustment')
            declared_status = safe_response.get('status')
            event['status'] = 'ok' if declared_status in {'ok', 'success'} and rows else (declared_status or 'error')
        event['verifiable'] = self.ledger.verify_mcp_event(event)
        if not event['verifiable']:
            event['authenticity_reason'] = 'historical MCP evidence incomplete or mismatched'
        return response


class HistoricalDataUnavailable(RuntimeError):
    """An explicit historical acquisition prerequisite is unavailable."""


class HistoricalDataProvider:
    """Point-in-time historical data provider for research/backtest.

    Preferred data path:
      1) local ``security_master.csv`` with effective intervals, or
      2) Intel ``mcp_intel_get_historical_universe`` interval export, or
      3) Intel daily point-in-time universe pages.

    Current-day wencai/F10 fallbacks are explicitly diagnostic only and are never
    silently promoted to research-grade data.
    """

    def __init__(self, root: Path, mcp: MCPInvoker | None = None, *, use_cache: bool = True,
                 ledger: ConsumedInputLedger | None = None, read_only_mcp: bool = False,
                 strict_paid_source: bool = False, historical_range: tuple[str, str] | None = None,
                 historical_max_bars: int | None = None, historical_request_end: str | None = None):
        self.root = Path(root).absolute()
        self.ledger = ledger or ConsumedInputLedger(self.root)
        self._request_origins: dict[Any, list[int]] = {}
        self.strict_paid_source = bool(strict_paid_source)
        self.historical_range = historical_range
        self.historical_max_bars = historical_max_bars
        self.historical_request_end = historical_request_end
        self._historical_tool_available: bool | None = None
        self.mcp = _ProvenanceMCP(mcp, self.ledger, read_only_mcp) if mcp is not None else None
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
        self._status_intervals: dict[str, list[dict[str, Any]]] = {}
        self._sector_intervals_map: dict[str, list[dict[str, Any]]] = {}
        self._sector_constituents_cache: dict[tuple[str, str], list[str]] = {}
        self._raw_bars_mem: dict[str, list[dict[str, Any]]] = {}
        self.corporate_actions = CorporateActionEngine()
        ca_path = self.root / "data/backtest/corporate_actions.csv"
        for row in self._read_csv(ca_path):
            if row.get("symbol") and row.get("ex_date"):
                self.corporate_actions.add_action(CorporateAction.from_dict(row))
        self._period_universe: UniverseInfo | None = None
        self._daily_point_in_time_enabled = False
        self._universe_mode = "prefer_point_in_time"
        self._max_universe = 0
        self._load_status_intervals()
        self._load_sector_intervals()

    def _read_csv(self, path: Path) -> list[dict[str, Any]]:
        try:
            return self.ledger.read(path, kind=path.stem,
                parser=lambda raw: list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))))
        except FileNotFoundError:
            return []

    @staticmethod
    def _safe(symbol: str) -> str:
        return symbol.replace("/", "_").replace(".", "_").replace(":", "_")

    def _cache_file(self, kind: str, key: str) -> Path:
        p = self.cache_root / kind
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{self._safe(key)}.json"

    def _load_json(self, path: Path) -> Any:
        try:
            return self.ledger.read(path, kind=path.parent.name,
                parser=lambda raw: json.loads(raw.decode("utf-8")))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return None

    def _save_json(self, path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _load_status_intervals(self) -> None:
        path = self.root / "data" / "backtest" / "historical_status_intervals.csv"
        for row in self._read_csv(path):
            sym = str(row.get("symbol") or "")
            if sym:
                self._status_intervals.setdefault(sym, []).append(row)

    def status_on(self, symbol: str, as_of: str) -> str:
        intervals = self._status_intervals.get(symbol, [])
        for item in intervals:
            start = _date_str(item.get("effective_from"))
            end = _date_str(item.get("effective_to"))
            if start and as_of < start:
                continue
            if end and as_of > end:
                continue
            return str(item.get("status") or "TRADABLE").upper()
        return "TRADABLE"

    def _load_sector_intervals(self) -> None:
        path = self.root / "data" / "backtest" / "historical_sector_intervals.csv"
        for row in self._read_csv(path):
            sym = str(row.get("symbol") or "")
            if sym:
                self._sector_intervals_map.setdefault(sym, []).append(row)

    def sector_constituents_on(self, sector_code: str, as_of: str) -> list[str]:
        cache_key = (str(sector_code), as_of)
        if cache_key in self._sector_constituents_cache:
            return self._sector_constituents_cache[cache_key]
        out: list[str] = []
        for sym, intervals in self._sector_intervals_map.items():
            for item in intervals:
                if str(item.get("sector_code")) == str(sector_code):
                    start = _date_str(item.get("effective_from"))
                    end = _date_str(item.get("effective_to"))
                    if start and as_of < start:
                        continue
                    if end and as_of > end:
                        continue
                    if self.eligible_on(sym, as_of):
                        out.append(sym)
                    break
        self._sector_constituents_cache[cache_key] = out
        return out

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
                    "data_missing": False,
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
            data_missing = _as_bool(x.get("data_missing"), False)
            tradable_default = listed and not delisting_period and not risk_warning and not suspended and not data_missing
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
                "data_missing": data_missing,
                "available_at": x.get("available_at"),
                "raw": x,
            })
        return out

    def _is_active_record(self, rec: dict[str, Any], as_of: str) -> bool:
        start = str(rec.get("active_from") or rec.get("listing_date") or "")
        end = str(rec.get("active_to") or rec.get("delisting_date") or "")
        if start and as_of < start:
            return False
        if end and as_of > end:
            return False
        if not bool(rec.get("listed", True)):
            return False
        sym = str(rec.get("symbol") or "")
        status = self.status_on(sym, as_of)
        if status == "DELISTED":
            return False
        return True

    def _record_eligible(self, rec: dict[str, Any], as_of: str) -> bool:
        if not self._is_active_record(rec, as_of):
            return False
        if bool(rec.get("data_missing", False)):
            return False
        sym = str(rec.get("symbol") or "")
        status = self.status_on(sym, as_of)
        if status in {"ST", "*ST", "SUSPENDED", "DELISTING", "DELISTED"}:
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
        items = self._normalize_membership_items(self._read_csv(path))
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
            records = [x for x in snap.records if self._is_active_record(x, as_of)]
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
                    if not self._is_active_record(rec, as_of):
                        continue
                    status = self.status_on(sym, as_of)
                    is_st = status in {"ST", "*ST"}
                    is_susp = status == "SUSPENDED"
                    is_delist = status == "DELISTING"
                    is_missing = bool(rec.get("data_missing", False))
                    is_delisted = (status == "DELISTED") or (not bool(rec.get("listed", True)))
                    rec_dyn = dict(rec)
                    rec_dyn["st"] = is_st
                    rec_dyn["risk_warning"] = is_st
                    rec_dyn["suspended"] = is_susp
                    rec_dyn["delisting_period"] = is_delist
                    rec_dyn["status"] = status
                    rec_dyn["data_missing"] = is_missing
                    market_tradable = (not is_delisted) and (not is_susp) and (not is_missing)
                    strategy_eligible = market_tradable and (not is_st) and (not is_delist)
                    rec_dyn["market_tradable"] = market_tradable
                    rec_dyn["strategy_eligible"] = strategy_eligible
                    rec_dyn["tradable"] = market_tradable
                    chosen = rec_dyn
                    rows.append(rec_dyn)
                    break
                if chosen is not None:
                    self._daily_record_mem[(as_of, sym)] = chosen
            if self._max_universe:
                rows = rows[: self._max_universe]
            return rows
        return [{"symbol": s, "tradable": True, "market_tradable": True, "strategy_eligible": self.eligible_on(s, as_of)} for s in (fallback_symbols or []) if self.eligible_on(s, as_of)]

    def active_symbols_on(self, as_of: str, fallback_symbols: list[str] | None = None) -> list[str]:
        return [str(x["symbol"]) for x in self.active_records_on(as_of, fallback_symbols)]

    def tradable_records_on(self, as_of: str, fallback_symbols: list[str] | None = None) -> list[dict[str, Any]]:
        return [x for x in self.active_records_on(as_of, fallback_symbols) if x.get("market_tradable")]

    def tradable_symbols_on(self, as_of: str, fallback_symbols: list[str] | None = None) -> list[str]:
        return [str(x["symbol"]) for x in self.tradable_records_on(as_of, fallback_symbols)]

    def eligible_records_on(self, as_of: str, fallback_symbols: list[str] | None = None) -> list[dict[str, Any]]:
        return [x for x in self.active_records_on(as_of, fallback_symbols) if x.get("strategy_eligible")]

    def eligible_symbols_on(self, as_of: str, fallback_symbols: list[str] | None = None) -> list[str]:
        return [str(x["symbol"]) for x in self.eligible_records_on(as_of, fallback_symbols)]

    def is_market_tradable(self, symbol: str, as_of: str) -> bool:
        rec = self._daily_record_mem.get((as_of, symbol))
        if rec is not None:
            return bool(rec.get("market_tradable", True))
        status = self.status_on(symbol, as_of)
        return status not in {"SUSPENDED", "DELISTED"}

    def is_strategy_eligible(self, symbol: str, as_of: str) -> bool:
        rec = self._daily_record_mem.get((as_of, symbol))
        if rec is not None:
            return bool(rec.get("strategy_eligible", True))
        status = self.status_on(symbol, as_of)
        return status not in {"ST", "*ST", "SUSPENDED", "DELISTING", "DELISTED"}

    def listing_date_on(self, symbol: str) -> str:
        recs = self._membership.get(symbol, [])
        return str(recs[0].get("listing_date", "") if recs else "")

    def daily_universe_meta(self, as_of: str, fallback_symbols: list[str] | None = None) -> dict[str, Any]:
        records = self.active_records_on(as_of, fallback_symbols)
        tradable_count = sum(1 for x in records if x.get("market_tradable"))
        eligible_count = sum(1 for x in records if x.get("strategy_eligible"))
        missing_count = sum(1 for x in records if x.get("data_missing"))
        st_count = sum(1 for x in records if x.get("risk_warning"))
        suspended_count = sum(1 for x in records if x.get("suspended"))
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
            "tradable_symbols": tradable_count,
            "market_tradable_symbols": tradable_count,
            "strategy_eligible_symbols": eligible_count,
            "missing_data_symbols": missing_count,
            "st_symbols": st_count,
            "suspended_symbols": suspended_count,
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
        path = self.root / universe_file if not Path(universe_file).is_absolute() else Path(universe_file)
        syms: list[str] = []
        if path.exists():
            text = self.ledger.read(path, kind="universe", parser=lambda raw: raw.decode("utf-8-sig"))
            for line in text.splitlines():
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

    def historical_bars(self, symbols: list[str], start_date: str, end_date: str, *,
                        period: str = 'D', adjustment: str = 'none',
                        max_bars_per_symbol: int = 1000) -> dict[str, list[dict[str, Any]]]:
        """Fetch a date-bounded provider export without cache or live-data fallback."""
        if self.mcp is None:
            raise RuntimeError('historical bars require an explicit MCP invoker')
        response = self.mcp.invoke('mcp_intel_historical_bars', symbols=symbols,
            start_date=start_date, end_date=end_date, period=period,
            adjustment=adjustment, max_bars_per_symbol=max_bars_per_symbol)
        bars = response.get('bars') if isinstance(response, dict) else None
        if not isinstance(bars, dict):
            raise RuntimeError('historical bars response missing symbol map')
        return {symbol: normalize_bars(bars.get(symbol, [])) for symbol in symbols}

    def _historical_signal_rows(self, symbol: str, adjustment: str, count: int) -> list[dict[str, Any]]:
        list_tools = getattr(self.mcp.invoker, "list_tools", None)
        if self._historical_tool_available is None and callable(list_tools):
            advertised = {tool.get("name") for tool in list_tools("intel")}
            self._historical_tool_available = bool(
                {"historical_bars", "mcp_intel_historical_bars"} & advertised)
        if self._historical_tool_available is False:
            reason = "HISTORICAL_TOOL_UNAVAILABLE:mcp_intel_historical_bars"
            self.ledger.append(kind="historical_tool", logical_key="mcp_intel_historical_bars",
                source_type="mcp", status="missing", error=reason)
            raise HistoricalDataUnavailable(reason)
        start_date, end_exclusive = self.historical_range
        end_date = self.historical_request_end or (
            datetime.fromisoformat(end_exclusive).date() - timedelta(days=1)).isoformat()
        response = self.mcp.invoke("mcp_intel_historical_bars", symbols=[symbol],
            start_date=start_date, end_date=end_date, period="D", adjustment=adjustment,
            max_bars_per_symbol=self.historical_max_bars or count, provider="tdx",
            strict_paid_source=self.strict_paid_source)
        if not isinstance(response, dict) or response.get("source") != "tdx" or response.get("fallback_source"):
            raise HistoricalDataUnavailable("historical bars source mismatch: expected tdx without fallback")
        accepted = {"none", "raw"} if adjustment == "none" else {adjustment}
        if response.get("adjustment") not in accepted:
            raise HistoricalDataUnavailable("historical bars adjustment mismatch: expected explicit requested mode")
        bars = response.get("bars")
        if not isinstance(bars, dict) or not isinstance(bars.get(symbol), list):
            raise HistoricalDataUnavailable("historical bars response missing symbol map")
        rows = normalize_bars(bars[symbol])
        if any(not start_date <= row["date"] <= end_date for row in rows):
            raise HistoricalDataUnavailable("bars outside requested historical range")
        if adjustment != "none" and self.strict_paid_source:
            semantics = response.get("adjustment_semantics")
            factors = response.get("adjustment_factors")
            if not isinstance(semantics, dict) or not isinstance(factors, dict):
                raise HistoricalDataUnavailable("historical bars adjustment evidence missing")
            as_of = semantics.get("as_of")
            if (semantics.get("mode") != adjustment or semantics.get("point_in_time") is not True
                    or not isinstance(as_of, str) or not start_date <= as_of <= end_date):
                raise HistoricalDataUnavailable("historical bars adjustment evidence has ambiguous or future semantics")
            symbol_factors = factors.get(symbol)
            if not isinstance(symbol_factors, dict):
                raise HistoricalDataUnavailable("historical bars adjustment evidence missing symbol factors")
            for row in rows:
                try:
                    evidence = symbol_factors[row["date"]]
                    if (not isinstance(evidence, dict) or evidence.get("point_in_time") is not True
                            or not isinstance(evidence.get("dataset_version"), str)
                            or not evidence["dataset_version"].strip()
                            or not isinstance(evidence.get("as_of"), str)
                            or not start_date <= evidence["as_of"] <= row["date"]):
                        raise ValueError
                    factor = float(evidence["factor"])
                    if not 0 < factor < float("inf"):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    raise HistoricalDataUnavailable("historical bars adjustment evidence missing valid date factor") from None
        return rows

    def bars(self, symbol: str, *, count: int = 900) -> list[dict[str, Any]]:
        if symbol in self._bars_mem:
            return self._bars_mem[symbol]
        if self.historical_range and self.mcp is not None:
            rows = self._historical_signal_rows(symbol, "qfq", count)
        else:
            path = self._cache_file("bars", symbol)
            if self.use_cache and path.exists():
                rows = normalize_bars(self._load_json(path))
            else:
                csv_path = self.root / "data" / "backtest" / "prices" / f"{self._safe(symbol)}.csv"
                if csv_path.exists():
                    rows = normalize_bars(self._read_csv(csv_path))
                elif self.mcp is not None:
                    raw = self.mcp.invoke("mcp_intel_tdx_kline", symbol=symbol, period="D", count=min(max(count, 1000), 1000))
                    rows = normalize_bars(raw)
                    if not self.strict_paid_source and len(rows) < count:
                        raw = self.mcp.invoke("mcp_intel_fetch_kline", symbol=symbol, period="D", count=min(max(count, 1000), 1000))
                        fetched = normalize_bars(raw)
                        if len(fetched) > len(rows):
                            rows = fetched
                else:
                    self.ledger.append(kind='adjusted_bars', logical_key=symbol, path=str(csv_path),
                        requested_range=self.ledger.requested_range, source_type='physical', status='missing')
                    rows = []
        self._bars_mem[symbol] = rows
        if rows and self.use_cache:
            self._save_json(self._cache_file("bars", symbol), rows)
        if not rows:
            self.warnings.append(f"NO_BARS:{symbol}")
        return rows

    def raw_bars(self, symbol: str, *, count: int = 900) -> list[dict[str, Any]]:
        """Return raw unadjusted historical OHLC bars for execution accounting."""
        if symbol in self._raw_bars_mem:
            return self._raw_bars_mem[symbol]
        if self.historical_range and self.mcp is not None:
            rows = self._historical_signal_rows(symbol, "none", count)
            self._raw_bars_mem[symbol] = rows
            return rows
        raw_csv = self.root / "data" / "backtest" / "raw_prices" / f"{self._safe(symbol)}.csv"
        if raw_csv.exists():
            rows = normalize_bars(self._read_csv(raw_csv))
            if rows:
                self._raw_bars_mem[symbol] = rows
                return rows
        raw_path = self._cache_file("raw_bars", symbol)
        if self.use_cache and raw_path.exists():
            rows = normalize_bars(self._load_json(raw_path))
            if rows:
                self._raw_bars_mem[symbol] = rows
                return rows
        if self.mcp is not None:
            if self.strict_paid_source:
                raw = self.mcp.invoke("mcp_intel_tdx_kline", symbol=symbol, period="D", count=count)
                rows = normalize_bars(raw)
            else:
                raw = self.mcp.invoke("mcp_intel_fetch_kline", symbol=symbol, period="D", count=count)
                rows = normalize_bars(raw)
                if not rows:
                    raw = self.mcp.invoke("mcp_intel_tdx_kline", symbol=symbol, period="D", count=count)
                    rows = normalize_bars(raw)
            self._raw_bars_mem[symbol] = rows
            return rows
        return []
    def adjustment_factor_on(self, symbol: str, as_of: str) -> float:
        """Return adjustment factor (adj_close / raw_close) on or before as_of date.
        Used to convert signal-space stop/target prices to raw execution space.
        """
        adj_bars = self.bars(symbol)
        raw_b = self.raw_bars(symbol)
        if not adj_bars or not raw_b:
            return 1.0
        adj_close = None
        for b in reversed(adj_bars):
            if str(b.get("date") or b.get("time", "")) <= as_of:
                adj_close = float(b.get("close", 0) or 0)
                break
        raw_close = None
        for b in reversed(raw_b):
            if str(b.get("date") or b.get("time", "")) <= as_of:
                raw_close = float(b.get("close", 0) or 0)
                break
        if adj_close is not None and raw_close is not None and raw_close > 0:
            return adj_close / raw_close
        return 1.0


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
        intervals = self._sector_intervals_map.get(symbol, [])
        for item in intervals:
            start = _date_str(item.get("effective_from"))
            end = _date_str(item.get("effective_to"))
            if start and as_of < start:
                continue
            if end and as_of > end:
                continue
            if item.get("sector_code") or item.get("sector_name"):
                val = {
                    "name": item.get("sector_name"),
                    "code": item.get("sector_code"),
                    "source": "historical_sector_interval",
                    "point_in_time": True,
                }
                self._sector_on_mem[key] = val
                return val
        rec = self._daily_record_mem.get(key)
        if rec and (rec.get("industry_code") or rec.get("industry_name")):
            val = {"name": rec.get("industry_name"), "code": rec.get("industry_code"), "source": "historical_universe", "point_in_time": True}
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


# Requests remain linked to their original bytes across preflight/execution memory hits.
for _method in ("load_universe_for_period", "load_universe", "bars", "raw_bars", "historical_bars",
                "active_records_on", "sector_info_on", "sector_info", "sector_bars",
                "_fetch_daily_universe"):
    setattr(HistoricalDataProvider, _method,
            _record_request(getattr(HistoricalDataProvider, _method)))
