"""Tests for backup and migration (P15)."""

import zipfile

from app.core.db import SCHEMA_VERSION, get_engine, get_schema_version, run_migrations


class TestMigration:
    def test_schema_version_recorded(self, tmp_db):
        """After init_db, schema version should be current."""
        engine = get_engine()
        version = get_schema_version(engine)
        assert version == SCHEMA_VERSION

    def test_migrations_idempotent(self, tmp_db):
        """Running migrations again should apply nothing."""
        engine = get_engine()
        applied = run_migrations(engine)
        assert applied == []

    def test_schema_version_incremental(self, tmp_db):
        """Verify schema version is >= 2 (v0.5)."""
        engine = get_engine()
        assert get_schema_version(engine) >= 2


class TestBackup:
    def test_backup_create(self, tmp_db, tmp_path):
        """Backup should create a zip containing expected files."""
        import app.core.config as cfg_mod

        # Create config in test REPO_ROOT
        config_dir = cfg_mod.REPO_ROOT / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text("test: true")

        # Create a library file
        papers_dir = cfg_mod.REPO_ROOT / "data" / "library" / "papers" / "1"
        papers_dir.mkdir(parents=True, exist_ok=True)
        (papers_dir / "paper.pdf").write_text("fake pdf")

        from app.pipelines.backup import create_backup

        out = str(tmp_path / "out" / "test_backup.zip")
        (tmp_path / "out").mkdir()
        result = create_backup(output_path=out)

        assert result["files"] >= 2  # config + PDF at minimum
        assert result["size_bytes"] > 0

        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
            assert any("config.yaml" in n for n in names)
            assert any("paper.pdf" in n for n in names)

    def test_backup_no_pdf(self, tmp_db, tmp_path):
        """Backup with --no-pdf should exclude PDFs."""
        import app.core.config as cfg_mod

        config_dir = cfg_mod.REPO_ROOT / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text("test: true")

        papers_dir = cfg_mod.REPO_ROOT / "data" / "library" / "papers" / "1"
        papers_dir.mkdir(parents=True, exist_ok=True)
        (papers_dir / "paper.pdf").write_text("fake pdf")
        (papers_dir / "notes.md").write_text("notes")

        from app.pipelines.backup import create_backup

        out = str(tmp_path / "out" / "no_pdf.zip")
        (tmp_path / "out").mkdir(exist_ok=True)
        create_backup(output_path=out, no_pdf=True)

        with zipfile.ZipFile(out, "r") as zf:
            names = zf.namelist()
            assert not any(n.endswith(".pdf") for n in names)
            assert any("notes.md" in n for n in names)
