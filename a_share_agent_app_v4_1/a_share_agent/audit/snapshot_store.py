from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

from ..utils import canonical_json, redact, sha256_hex


class SnapshotStore:
    def __init__(self, audit_root: Path, never_log: set[str] | None = None, hash_fields: set[str] | None = None):
        self.audit_root = audit_root
        self.never_log = never_log or set()
        self.hash_fields = hash_fields or set()

    def _dir(self, trade_date: str) -> Path:
        y, m, d = trade_date.split("-")
        p = self.audit_root / y / m / d / "snapshots"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, trade_date: str, payload: Any) -> dict[str, str | int]:
        safe = redact(payload, self.never_log, self.hash_fields)
        raw = canonical_json(safe).encode("utf-8")
        digest = sha256_hex(raw)
        path = self._dir(trade_date) / f"{digest}.json.gz"
        if not path.exists():
            tmp = path.with_suffix(path.suffix + ".tmp")
            with gzip.open(tmp, "wb", compresslevel=6) as fh:
                fh.write(raw)
            tmp.replace(path)
        return {"snapshot_ref": f"sha256:{digest}", "sha256": digest, "path": str(path), "bytes": len(raw)}

    def get(self, trade_date: str, ref: str) -> Any:
        digest = ref.split(":", 1)[-1]
        path = self._dir(trade_date) / f"{digest}.json.gz"
        with gzip.open(path, "rb") as fh:
            raw = fh.read()
        if sha256_hex(raw) != digest:
            raise ValueError(f"snapshot hash mismatch: {ref}")
        return json.loads(raw)
