"""Authenticated whole-generation policy publication, historical selection and rollback.

The integrity key is supplied by the trusted deployment, never read from the
policy/audit store. Unsigned loose files cannot enable patterns. One store writer
must serialize publications; readers consume a single atomically replaced pointer.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path

from .enablement import PolicyEntry, PolicyConfig, PolicyStatus, _safe_path, _compute_file_hash, _timestamp, _key, load_config_from_yaml, verify_physical_artifact

logger = logging.getLogger(__name__)


def _canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()

def decision_key(signal, market_context, sector_context):
    values = (market_context.get("regime"), sector_context.get("lifecycle"),
              signal.get("pattern_id"), signal.get("pattern_version"))
    return list(values) if all(isinstance(v, str) and v.strip() for v in values) else None

def policy_decision(*, as_of=None, key=None, quality=None, reason="missing_or_invalid_policy"):
    """One JSON-safe decision shape, including unavailable metadata explicitly."""
    return dict(allowed=False, reason=reason, key=key, evidence_id=None,
                manifest_hash=None, policy_hash=None, policy_generation=None,
                as_of=as_of, quality=quality or {"market": None, "sector": None},
                status=None, issued_at=None, expires_at=None)


def _atomic_json(path, data):
    path = _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical(data))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


class PolicyLoader:
    def __init__(self, directories=None, *, config=None, integrity_key=None, trusted_identity=None, runtime_config=None):
        self.config = config or PolicyConfig()
        self._directories = [_safe_path(d) for d in (self.config.directories if directories is None else directories)]
        self._integrity_key = integrity_key
        self._identity = trusted_identity
        self.runtime_config = runtime_config

    @classmethod
    def from_runtime_config(cls, config):
        settings = config.strategy_router.get("policy", {})
        if not settings.get("enabled", False):
            return None
        policy = load_config_from_yaml(config.project_root / "config" / "pattern_enablement.yaml")
        secret = os.environ.get(settings.get("integrity_key_env", "BBB_POLICY_INTEGRITY_KEY"))
        identity = os.environ.get(settings.get("trusted_identity_env", "BBB_POLICY_TRUSTED_IDENTITY"))
        return cls([config.project_root / p for p in policy.directories], config=policy,
                   integrity_key=secret.encode() if secret else None, trusted_identity=identity, runtime_config=config)

    def authorize_entry(self, signal, *, market_context, sector_context, as_of=None):
        """Authenticate an exact identity at the decision time, never a route label."""
        result = policy_decision()
        try:
            result["quality"] = {"market": market_context.get("data_quality", {}).get("state"),
                                 "sector": sector_context.get("data_quality", {}).get("state")}
            result["key"] = decision_key(signal, market_context, sector_context)
            at = time.time() if as_of is None else _timestamp(as_of, "as_of")
            result["as_of"] = at
            if result["key"] is None:
                raise ValueError("incomplete exact identity")
            if any(v.upper() in {"UNKNOWN", "DEGRADED", "PANIC"} for v in result["key"][:2]):
                result["reason"] = "unsafe_context"
                return result
            key = _key(result["key"])
            if any(c.get("data_quality", {}).get("state") != "ok" for c in (market_context, sector_context)):
                result["reason"] = "degraded_context"
                return result
            try:
                selected = self._selected(at, with_generation=True)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                return result
            entries = [entry for entry, generation in selected]
            matching = [(entry, generation) for entry, generation in selected if entry.key == key]
            if matching:
                entry, generation = matching[-1]
                if self.runtime_config is not None:
                    verify_physical_artifact(Path(entry.manifest_path).parent, entry.key, config=self.runtime_config)
                allowed = str(key) in self._filter_active(entries, at)
                result.update(allowed=allowed, reason="approved_exact_policy" if allowed else
                              entry.reason or f"policy {entry.status.value} or outside validity window",
                              evidence_id=entry.evidence_id, manifest_hash=entry.manifest_hash,
                              policy_hash=hashlib.sha256(_canonical(entry.to_dict())).hexdigest(),
                              policy_generation=generation, status=entry.status.value,
                              issued_at=entry.issued_at, expires_at=entry.expires_at)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            result["reason"] = "invalid_exact_key_or_context"
        return result

    def filter_signals(self, signals, *, market_context, sector_context, as_of=None, decisions=None):
        filtered = []
        for signal in signals:
            if signal.get("strength") == "exit":
                filtered.append(signal)
                continue
            decision = self.authorize_entry(signal, market_context=market_context,
                                            sector_context=sector_context, as_of=as_of)
            if decisions is not None:
                decisions.append(decision)
            if decision["allowed"]:
                filtered.append(signal)
        return filtered

    def _require_trust(self):
        if not isinstance(self._integrity_key, bytes) or len(self._integrity_key) < 32 or not isinstance(self._identity, str) or not self._identity.strip():
            raise ValueError("Trusted identity and external integrity key (minimum 32 bytes) required")

    def _signed(self, data):
        self._require_trust()
        body = dict(data, identity=self._identity, schema_version=1)
        return dict(body, signature=hmac.new(self._integrity_key, _canonical(body), hashlib.sha256).hexdigest())

    def _verify(self, data):
        self._require_trust()
        if not isinstance(data, dict):
            raise ValueError("Signed record must be object")
        body = dict(data)
        signature = body.pop("signature", None)
        if body.get("identity") != self._identity or body.get("schema_version") != 1 or not isinstance(signature, str):
            raise ValueError("Untrusted record identity/schema/signature")
        expected = hmac.new(self._integrity_key, _canonical(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Record integrity signature mismatch")
        return body

    def _read_pointer(self, root, pointer=None):
        path = _safe_path(pointer or root / "active")
        if path.parent != root:
            raise ValueError("Pointer must be inside generation root")
        data = self._verify(json.loads(path.read_text()))
        if data.get("kind") != "policy_pointer" or not isinstance(data.get("history"), list) or not data["history"]:
            raise ValueError("Invalid generation pointer")
        last = float("-inf")
        seen = set()
        for ref in data["history"]:
            ts = _timestamp(ref["timestamp"], "publication timestamp")
            if ts < last or ref["generation"] in seen:
                raise ValueError("Invalid publication history")
            last = ts
            seen.add(ref["generation"])
        return data["history"]

    def _load_generation(self, root, ref):
        name = ref["generation"]
        if not isinstance(name, str) or Path(name).name != name or not name.startswith("gen_"):
            raise ValueError("Generation path escape")
        directory = _safe_path(root / name)
        path = _safe_path(directory / "generation.json")
        if _compute_file_hash(path) != ref["manifest_hash"]:
            raise ValueError("Generation manifest hash mismatch")
        manifest = self._verify(json.loads(path.read_text()))
        if manifest.get("kind") != "policy_generation" or manifest.get("generation_id") != name or manifest.get("generation_timestamp") != ref["timestamp"]:
            raise ValueError("Generation identity/time mismatch")
        members = manifest.get("members")
        if not isinstance(members, dict) or type(manifest.get("entry_count")) is not int or manifest["entry_count"] != len(members):
            raise ValueError("Generation member count mismatch")
        actual_files = {p.name for p in directory.iterdir()}
        if actual_files != set(members) | {"generation.json"}:
            raise ValueError("Generation exact member set mismatch")
        entries = []
        keys = set()
        for filename, commitment in members.items():
            if Path(filename).name != filename or not filename.endswith(".json"):
                raise ValueError("Generation member path invalid")
            member = _safe_path(directory / filename)
            if _compute_file_hash(member) != commitment["sha256"]:
                raise ValueError("Generation member hash mismatch")
            entry = PolicyEntry.from_snapshot(member)
            if list(entry.key) != commitment["key"] or entry.key in keys:
                raise ValueError("Generation exact key mismatch/duplicate")
            if entry.issued_at > ref["timestamp"] or (entry.approved_at is not None and entry.approved_at > ref["timestamp"]):
                raise ValueError("Generation published before policy/approval")
            keys.add(entry.key)
            entries.append(entry)
        return entries

    def publish_generation(self, entries, generation_dir, pointer_file=None):
        self._require_trust()
        root = _safe_path(generation_dir)
        root.mkdir(parents=True, exist_ok=True)
        pointer = _safe_path(pointer_file or root / "active")
        if pointer.parent != root:
            raise ValueError("Pointer outside generation root")
        history = self._read_pointer(root, pointer) if pointer.exists() else []
        # Validate every entry before writing any published state.
        entries = [PolicyEntry.from_dict(e.to_dict()) for e in entries]
        if len({e.key for e in entries}) != len(entries):
            raise ValueError("Duplicate policy key")
        ts = time.time()
        if history and ts < history[-1]["timestamp"]:
            raise ValueError("Publication clock moved backwards")
        if any(e.issued_at > ts or (e.approved_at is not None and e.approved_at > ts) for e in entries):
            raise ValueError("Future policy publication")
        name = "gen_" + uuid.uuid4().hex
        staged = Path(tempfile.mkdtemp(prefix=".tmp-", dir=root))
        members = {}
        for entry in entries:
            filename = hashlib.sha256(_canonical(list(entry.key))).hexdigest() + ".json"
            _atomic_json(staged / filename, entry.to_dict())
            members[filename] = {"sha256": _compute_file_hash(staged / filename), "key": list(entry.key)}
        manifest = self._signed(dict(kind="policy_generation", generation_id=name, generation_timestamp=ts, entry_count=len(entries), members=members))
        _atomic_json(staged / "generation.json", manifest)
        final = root / name
        staged.rename(final)
        ref = dict(generation=name, manifest_hash=_compute_file_hash(final / "generation.json"), timestamp=ts)
        self._load_generation(root, ref)
        # Persist audit before the pointer; a failed audit leaves the old pointer intact.
        if self.config.enable_snapshots:
            self._save_generation_audit(root, ref, "generation_publication")
        _atomic_json(pointer, self._signed(dict(kind="policy_pointer", history=history+[ref])))
        return dict(generation_path=str(final), timestamp=ts, entry_count=len(entries), manifest_hash=ref["manifest_hash"])

    def _selected(self, as_of, *, with_generation=False):
        # Configured later roots have precedence, but an invalid root fails the entire table closed.
        selected = []
        for root in self._directories:
            if not root.exists():
                continue
            pointer = root / "active"
            if not pointer.exists():
                if pointer.is_symlink():
                    raise ValueError("Pointer symlink rejected")
                continue
            history = self._read_pointer(root)
            eligible = [ref for ref in history if ref["timestamp"] <= as_of]
            if not eligible:
                continue
            for ref in reversed(eligible):
                age = self.config.max_file_age_seconds
                if age and as_of - ref["timestamp"] > age:
                    raise ValueError("Policy generation stale")
                try:
                    entries = self._load_generation(root, ref)
                except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                    logger.warning("Skipping invalid policy generation %s: %s", ref["generation"], exc)
                    continue
                if with_generation:
                    selected.extend((entry, ref["generation"]) for entry in entries)
                else:
                    selected.extend(entries)
                break
            else:
                raise ValueError("No valid eligible policy generation")
        return selected

    def load_active(self, as_of=None):
        try:
            decision = time.time() if as_of is None else _timestamp(as_of, "as_of")
            return self._filter_active(self._selected(decision), decision)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning("Policy table disabled: %s", exc)
            return {}

    def load_all(self):
        try:
            return self._selected(time.time())
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.warning("Policy table disabled: %s", exc)
            return []

    def _filter_active(self, entries, as_of):
        blocked = {e.key for e in entries if e.status in (PolicyStatus.DISABLED, PolicyStatus.REVOKED)}
        merged = {str(e.key): e for e in entries}
        return {key: e for key, e in merged.items() if e.key not in blocked and e.is_valid_at(as_of)}

    def save_atomic(self, entry, path):
        """Write a non-authorizing standalone snapshot, useful for review/staging."""
        validated = PolicyEntry.from_dict(entry.to_dict())
        _atomic_json(Path(path), validated.to_dict())

    def _audit_root(self, audit_dir):
        root = _safe_path(audit_dir)
        if root != _safe_path(self.config.snapshot_directory):
            raise ValueError("Untrusted audit directory")
        if not self.config.enable_snapshots:
            raise ValueError("Audit snapshots disabled by configuration")
        return root

    def _save_generation_audit(self, root, ref, reason):
        audit_root = self._audit_root(self.config.snapshot_directory)
        self._load_generation(root, ref)
        ts = time.time()
        path = audit_root / f"audit_{ts:.6f}_{uuid.uuid4().hex}.json"
        _atomic_json(path, self._signed(dict(kind="policy_audit", timestamp=ts, reason=reason, generation_root=str(root), generation=dict(ref))))
        return dict(audit_path=str(path), policy_hash=ref["manifest_hash"], timestamp=ts)

    def save_audit_record(self, entry, audit_dir, reason="policy_change"):
        self._audit_root(audit_dir)
        # A record represents the full active generation, never only this member.
        for root in reversed(self._directories):
            if (root / "active").exists():
                ref = self._read_pointer(root)[-1]
                entries = self._load_generation(root, ref)
                if not any(e.to_dict() == entry.to_dict() for e in entries):
                    raise ValueError("Entry not a member of active generation")
                return self._save_generation_audit(root, ref, reason)
        raise ValueError("No complete published generation to audit")

    def _read_audit(self, audit_dir, snapshot_file=None, verify_hash=None):
        root = self._audit_root(audit_dir)
        if snapshot_file is None:
            candidates = sorted(root.glob("audit_*.json"), reverse=True)
            if not candidates:
                raise FileNotFoundError("No audit snapshots")
            path = candidates[0]
        else:
            relative = Path(snapshot_file)
            if relative.is_absolute() or ".." in relative.parts or relative.name != str(relative):
                raise ValueError("Audit snapshot path escapes trusted directory")
            path = root / relative
        path = _safe_path(path)
        record = self._verify(json.loads(path.read_text()))
        if record.get("kind") != "policy_audit":
            raise ValueError("Invalid audit kind")
        source_root = _safe_path(record["generation_root"])
        if source_root not in self._directories:
            raise ValueError("Untrusted audit generation root")
        ref = record["generation"]
        if ref["timestamp"] > _timestamp(record["timestamp"], "audit timestamp") or record["timestamp"] > time.time():
            raise ValueError("Future audit generation")
        if verify_hash is not None and ref["manifest_hash"] != verify_hash:
            raise ValueError("Audit hash does not match expected hash")
        entries = self._load_generation(source_root, ref)
        return entries

    def load_audit_snapshot(self, audit_dir, snapshot_file=None, verify_hash=None):
        """Return the complete validated generation, not a single policy."""
        return self._read_audit(audit_dir, snapshot_file, verify_hash)

    def rollback_generation(self, audit_dir, snapshot_file=None, *, generation_dir, pointer_file=None, verify_hash=None):
        entries = self._read_audit(audit_dir, snapshot_file, verify_hash)
        root = _safe_path(generation_dir)
        if root not in self._directories:
            raise ValueError("Rollback destination is untrusted")
        # Publish as a new event; preserve original history and never backdate restoration.
        return self.publish_generation(entries, root, pointer_file)
