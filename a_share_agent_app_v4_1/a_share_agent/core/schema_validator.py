from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


class SchemaValidationError(ValueError):
    pass


class SchemaRegistry:
    def __init__(self, schema_dir: str | Path):
        self.schema_dir = Path(schema_dir)
        self._cache: dict[str, Draft202012Validator] = {}

    def validate(self, schema_name: str, value: Any) -> None:
        validator = self._cache.get(schema_name)
        if not validator:
            schema = json.loads((self.schema_dir / schema_name).read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            self._cache[schema_name] = validator
        errors = sorted(validator.iter_errors(value), key=lambda e: list(e.path))
        if errors:
            lines = [f"{'/'.join(map(str,e.path)) or '<root>'}: {e.message}" for e in errors[:10]]
            raise SchemaValidationError("; ".join(lines))
