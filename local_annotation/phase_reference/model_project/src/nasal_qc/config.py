from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


def project_path(cfg: dict[str, Any], *parts: str) -> Path:
    return Path(cfg["project"]["root"]).joinpath(*parts)


def output_path(cfg: dict[str, Any], *parts: str) -> Path:
    return Path(cfg["project"]["output_dir"]).joinpath(*parts)

