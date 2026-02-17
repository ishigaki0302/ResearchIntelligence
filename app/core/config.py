"""Application configuration loader."""

from pathlib import Path
from typing import Any

import yaml


def _find_repo_root() -> Path:
    """Find the repo root by looking for configs/config.yaml upward."""
    p = Path(__file__).resolve()
    for parent in [p] + list(p.parents):
        if (parent / "configs" / "config.yaml").exists():
            return parent
    # Fallback: assume repo/ is two levels up from app/core/
    return Path(__file__).resolve().parent.parent.parent


REPO_ROOT = _find_repo_root()


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load config.yaml and resolve relative paths against REPO_ROOT."""
    if path is None:
        path = REPO_ROOT / "configs" / "config.yaml"
    with open(path) as f:
        cfg = yaml.safe_load(f)
    return cfg


def get_config() -> dict[str, Any]:
    """Singleton-ish config accessor."""
    if not hasattr(get_config, "_cache"):
        get_config._cache = load_config()
    return get_config._cache


def resolve_path(rel: str) -> Path:
    """Resolve a config-relative path to an absolute path."""
    return (REPO_ROOT / rel).resolve()
