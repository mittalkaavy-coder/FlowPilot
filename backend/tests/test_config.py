"""Tests that persistent storage paths do not depend on the launch directory."""
from core.config import BACKEND_DIR, Settings


def test_default_storage_paths_are_absolute_and_share_backend_root(monkeypatch, tmp_path):
    monkeypatch.delenv("FLOWPILOT_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("FLOWPILOT_DB_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = Settings()

    assert settings.storage_root == BACKEND_DIR.resolve()
    assert settings.db_path == (BACKEND_DIR / "flowpilot.db").resolve()
    assert settings.upload_dir == (BACKEND_DIR / "uploads").resolve()
    assert settings.report_dir == (BACKEND_DIR / "reports").resolve()
    assert all(path.is_absolute() for path in (
        settings.db_path,
        settings.upload_dir,
        settings.report_dir,
    ))


def test_relative_storage_overrides_are_stable_across_working_directories(
    monkeypatch, tmp_path
):
    storage_root = tmp_path / "shared-flowpilot-storage"
    monkeypatch.setenv("FLOWPILOT_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("FLOWPILOT_DB_PATH", "database/flowpilot.db")

    working_directories = [BACKEND_DIR.parent, BACKEND_DIR, tmp_path]
    configured_paths = []
    for working_directory in working_directories:
        monkeypatch.chdir(working_directory)
        settings = Settings()
        configured_paths.append((
            settings.storage_root,
            settings.db_path,
            settings.upload_dir,
            settings.report_dir,
        ))

    assert configured_paths[0] == configured_paths[1] == configured_paths[2]
    root, database, uploads, reports = configured_paths[0]
    assert root == storage_root.resolve()
    assert database == (root / "database/flowpilot.db").resolve()
    assert uploads == (root / "uploads").resolve()
    assert reports == (root / "reports").resolve()
    assert uploads.is_dir()
    assert reports.is_dir()


def test_sqlite_connection_uses_configured_absolute_database_path(
    monkeypatch, tmp_path
):
    from core import store

    storage_root = tmp_path / "database-root"
    monkeypatch.setenv("FLOWPILOT_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("FLOWPILOT_DB_PATH", "nested/flowpilot.db")
    settings = Settings()
    monkeypatch.setattr(store, "settings", settings)
    expected_database = (storage_root / "nested/flowpilot.db").resolve()

    monkeypatch.chdir(BACKEND_DIR.parent)
    first_connection = store.get_connection()
    first_connection.close()
    monkeypatch.chdir(tmp_path)
    second_connection = store.get_connection()
    second_connection.close()

    assert settings.db_path == expected_database
    assert expected_database.is_file()
    assert not (tmp_path / "flowpilot.db").exists()


def test_absolute_path_overrides_remain_supported(monkeypatch, tmp_path):
    database = tmp_path / "custom.db"
    monkeypatch.setenv("FLOWPILOT_DB_PATH", str(database))
    monkeypatch.delenv("FLOWPILOT_STORAGE_ROOT", raising=False)

    settings = Settings()

    assert settings.db_path == database.resolve()
    assert settings.storage_root == database.parent.resolve()
    assert settings.upload_dir == (database.parent / "uploads").resolve()
    assert settings.report_dir == (database.parent / "reports").resolve()
