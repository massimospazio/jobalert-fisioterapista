from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


def load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configurazione non valida: {path}")
    return data


def load_all() -> dict[str, dict[str, Any]]:
    return {
        "settings": load_yaml("settings.yaml"),
        "filters": load_yaml("filters.yaml"),
        "scoring": load_yaml("scoring.yaml"),
        "sources": load_yaml("sources.yaml"),
        "locations": load_yaml("locations.yaml"),
    }
