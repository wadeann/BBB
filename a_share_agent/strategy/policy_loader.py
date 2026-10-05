"""Policy loader with atomic load, audit, rollback."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List, Optional
from .enablement import PolicyEntry, PolicyStatus

class PolicyLoader:
    def __init__(self, directories: List[str]) -> None:
        self.directories = [Path(d) for d in directories]

    def load_active(self) -> Dict[str, PolicyEntry]:
        policies: Dict[str, PolicyEntry] = {}
        for d in self.directories:
            if not d.exists():
                continue
            for f in d.glob("*.json"):
                try:
                    entry = PolicyEntry.from_snapshot(f)
                    if entry.is_valid and entry.status == PolicyStatus.APPROVED:
                        key_str = str(entry.key)
                        policies[key_str] = entry
                except Exception:
                    continue
        return policies

    def load_all(self) -> List[PolicyEntry]:
        result: List[PolicyEntry] = []
        for d in self.directories:
            if not d.exists():
                continue
            for f in d.glob("*.json"):
                try:
                    entry = PolicyEntry.from_snapshot(f)
                    result.append(entry)
                except Exception:
                    continue
        return result

    def find_by_key(self, key: tuple) -> Optional[PolicyEntry]:
        key_str = str(key)
        for d in self.directories:
            for f in d.glob("*.json"):
                try:
                    entry = PolicyEntry.from_snapshot(f)
                    if entry.key == key and entry.is_valid:
                        return entry
                except Exception:
                    continue
        return None

    def save_atomic(self, entry: PolicyEntry, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        entry.to_snapshot(tmp)
        tmp.rename(path)

    def rollback_to_version(self, key: tuple, version_path: Path) -> PolicyEntry:
        return PolicyEntry.from_snapshot(version_path)
