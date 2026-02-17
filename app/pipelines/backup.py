"""Backup and restore utilities."""

import logging
import zipfile
from pathlib import Path

from app.core.config import get_config, resolve_path

logger = logging.getLogger(__name__)


def _get_repo_root():
    """Get current REPO_ROOT (supports test patching)."""
    import app.core.config as cfg_mod

    return cfg_mod.REPO_ROOT


def create_backup(
    output_path: str = "backup.zip",
    no_pdf: bool = False,
    no_cache: bool = False,
) -> dict:
    """Create a backup zip file containing DB, configs, and optionally library data.

    Returns: {"path": str, "files": int, "size_bytes": int}
    """
    cfg = get_config()
    repo_root = _get_repo_root()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    files_added = 0

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        # Always include DB
        db_path = resolve_path(cfg["storage"]["db_path"])
        if db_path.exists():
            zf.write(db_path, db_path.relative_to(repo_root))
            files_added += 1

        # Always include config
        config_path = repo_root / "configs" / "config.yaml"
        if config_path.exists():
            zf.write(config_path, config_path.relative_to(repo_root))
            files_added += 1

        # Library directory (papers, notes, text)
        library_dir = resolve_path(cfg["storage"]["library_dir"])
        if library_dir.exists():
            for fp in library_dir.rglob("*"):
                if not fp.is_file():
                    continue
                if no_pdf and fp.suffix.lower() == ".pdf":
                    continue
                zf.write(fp, fp.relative_to(repo_root))
                files_added += 1

        # Cache (embeddings, raw) — optional
        if not no_cache:
            for cache_key in ["cache_raw_dir", "cache_embeddings_dir"]:
                cache_dir_str = cfg["storage"].get(cache_key, "")
                if not cache_dir_str:
                    continue
                cache_dir = resolve_path(cache_dir_str)
                if cache_dir.exists():
                    for fp in cache_dir.rglob("*"):
                        if fp.is_file():
                            zf.write(fp, fp.relative_to(repo_root))
                            files_added += 1

    size = out.stat().st_size
    logger.info(f"Backup created: {out} ({files_added} files, {size} bytes)")
    return {"path": str(out), "files": files_added, "size_bytes": size}
