from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


_OPTIONS: dict[str, Any] = {}


def load() -> None:
    global _OPTIONS
    options_path = Path("/data/options.json")
    try:
        _OPTIONS = json.loads(options_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        _OPTIONS = {}
    for key, value in _OPTIONS.items():
        os.environ.setdefault(key.upper(), str(value))


def options() -> dict[str, Any]:
    return dict(_OPTIONS)
