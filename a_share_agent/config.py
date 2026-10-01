from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml

from .utils import redact, stable_hash


@dataclass(frozen=True)
class RuntimeConfig:
    project_root: Path
    runtime: dict[str, Any]
    defaults: dict[str, Any]
    schedule: dict[str, Any]
    permissions: dict[str, Any]
    strategy_router: dict[str, Any]
    logging: dict[str, Any]
    improvement: dict[str, Any]
    backtest: dict[str, Any]

    @property
    def mode(self) -> str:
        return str(self.runtime.get("mode", self.defaults.get("mode", "paper")))

    @property
    def config_hash(self) -> str:
        # Do not make audit metadata depend on secret material. Direct secrets are
        # supported for compatibility but are redacted before hashing; env-var names
        # remain part of the configuration identity.
        return stable_hash(redact({
            "runtime": self.runtime,
            "defaults": self.defaults,
            "schedule": self.schedule,
            "permissions": self.permissions,
            "strategy_router": self.strategy_router,
            "logging": self.logging,
            "improvement": self.improvement,
            "backtest": self.backtest,
        }))



def load_project_env(project_root: str | Path, filename: str = ".env") -> Path | None:
    """Load project-local .env without overriding already exported variables.

    This parser intentionally performs no shell interpolation: characters such as
    ``$`` remain literal, which is important for passwords/tokens. Values may be
    unquoted or wrapped in single/double quotes.
    """
    path = Path(project_root).resolve() / filename
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].lstrip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or not all(ch.isalnum() or ch == "_" for ch in key):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
    return path


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_config(project_root: str | Path) -> RuntimeConfig:
    root = Path(project_root).resolve()
    load_project_env(root)
    cfg = root / "config"
    return RuntimeConfig(
        project_root=root,
        runtime=_read_yaml(cfg / "runtime.yaml"),
        defaults=_read_yaml(cfg / "defaults.yaml"),
        schedule=_read_yaml(cfg / "schedule.yaml"),
        permissions=_read_yaml(cfg / "phase_permissions.yaml"),
        strategy_router=_read_yaml(cfg / "strategy_router.yaml"),
        logging=_read_yaml(cfg / "logging.yaml"),
        improvement=_read_yaml(cfg / "improvement.yaml"),
        backtest=_read_yaml(cfg / "backtest.yaml"),
    )
