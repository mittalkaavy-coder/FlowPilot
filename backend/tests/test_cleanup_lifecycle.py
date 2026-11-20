"""Retention cleanup safety and FastAPI lifespan tests."""
import os
import threading
import time
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.models.plan import WorkflowPlan
from core import store
from core.cleanup import cleanup_old_files
from core.config import Settings, settings
from main import app


def _age_file(path, hours=48):
    path.write_text("old artifact", encoding="utf-8")
    old_time = time.time() - hours * 3600
    os.utime(path, (old_time, old_time))


@pytest.fixture
def storage_layout(tmp_path, monkeypatch):
    root = tmp_path / "flowpilot-storage"
    uploads = root / "uploads"
    reports = root / "reports"
    uploads.mkdir(parents=True)
    reports.mkdir()
    monkeypatch.setattr(settings, "storage_root", root)
    monkeypatch.setattr(settings, "upload_dir", uploads)
    monkeypatch.setattr(settings, "report_dir", reports)
    monkeypatch.setattr(settings, "db_path", root / "flowpilot.db")
    monkeypatch.setattr(settings, "upload_retention_hours", 24)
    monkeypatch.setattr(settings, "report_retention_hours", 24)
    return root, uploads, reports


def test_cleanup_removes_expired_uploads_and_reports_but_keeps_recent_files(
    storage_layout,
):
    _, uploads, reports = storage_layout
    old_upload = uploads / "old.csv"
    old_report = reports / "old.md"
    recent_upload = uploads / "recent.csv"
    recent_report = reports / "recent.md"
    _age_file(old_upload, 48)
    _age_file(old_report, 48)
    recent_upload.write_text("recent", encoding="utf-8")
    recent_report.write_text("recent", encoding="utf-8")

    result = cleanup_old_files()

    assert result == {"uploads": 1, "reports": 1}
    assert not old_upload.exists()
    assert not old_report.exists()
    assert recent_upload.exists()
    assert recent_report.exists()


def test_active_workflow_upload_is_preserved_and_reports_are_deferred(
    storage_layout,
):
    root, uploads, reports = storage_layout
    file_id = str(uuid.uuid4())
    active_upload = uploads / f"{file_id}.csv"
    other_upload = uploads / f"{uuid.uuid4()}.csv"
    old_report = reports / f"{uuid.uuid4()}.md"
    _age_file(active_upload)
    _age_file(other_upload)
    _age_file(old_report)

    plan = WorkflowPlan(plan_id=str(uuid.uuid4()), goal="Active", steps=[])
    store.save_plan(plan, file_id)

    result = cleanup_old_files()

    assert result == {"uploads": 1, "reports": 0}
    assert active_upload.exists()
    assert not other_upload.exists()
    assert old_report.exists()
    assert (root / "flowpilot.db").is_file()


@pytest.mark.parametrize(
    ("status", "preserved"),
    [
        ("created", True),
        ("approved", True),
        ("executing", True),
        ("completed", False),
        ("failed", False),
    ],
)
def test_cleanup_respects_upload_and_report_for_each_workflow_state(
    storage_layout, status, preserved
):
    _, uploads, reports = storage_layout
    file_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())
    upload = uploads / f"{file_id}.csv"
    report = reports / f"{report_id}.md"
    _age_file(upload)
    _age_file(report)

    plan = WorkflowPlan(plan_id=str(uuid.uuid4()), goal=f"Workflow {status}", steps=[])
    store.save_plan(plan, file_id)
    if status != "created":
        if status in {"approved", "executing"}:
            store.approve_plan(plan.plan_id)
        store.update_plan_status(plan.plan_id, status)

    cleanup_old_files()

    assert upload.exists() is preserved
    # Reports are deferred while any workflow is pending or executing.
    assert report.exists() is preserved


def test_cleanup_never_removes_database_files(storage_layout):
    root, uploads, _ = storage_layout
    database = root / "flowpilot.db"
    plan = WorkflowPlan(plan_id=str(uuid.uuid4()), goal="Completed", steps=[])
    store.save_plan(plan, str(uuid.uuid4()))
    old_csv = uploads / f"{uuid.uuid4()}.csv"
    _age_file(old_csv)
    old_database_time = time.time() - 48 * 3600
    os.utime(database, (old_database_time, old_database_time))

    result = cleanup_old_files()

    assert result["uploads"] == 1
    assert database.is_file()


def test_cleanup_does_not_touch_files_outside_storage_root(
    storage_layout, monkeypatch, tmp_path
):
    root, _, reports = storage_layout
    outside_uploads = tmp_path / "outside" / "uploads"
    outside_uploads.mkdir(parents=True)
    outside_file = outside_uploads / f"{uuid.uuid4()}.csv"
    _age_file(outside_file)
    monkeypatch.setattr(settings, "upload_dir", outside_uploads)
    old_report = reports / f"{uuid.uuid4()}.md"
    _age_file(old_report)

    result = cleanup_old_files()

    assert result == {"uploads": 0, "reports": 1}
    assert outside_file.exists()
    assert root in reports.parents


def test_missing_storage_directories_are_safe(storage_layout, monkeypatch):
    root, _, _ = storage_layout
    monkeypatch.setattr(settings, "upload_dir", root / "uploads-missing")
    monkeypatch.setattr(settings, "report_dir", root / "reports-missing")

    assert cleanup_old_files() == {"uploads": 0, "reports": 0}


def test_cleanup_is_disabled_by_default_and_background_task_stops(
    storage_layout, monkeypatch
):
    _, uploads, _ = storage_layout
    old_upload = uploads / f"{uuid.uuid4()}.csv"
    _age_file(old_upload)
    monkeypatch.setattr(settings, "cleanup_enabled", False)

    with TestClient(app) as test_client:
        assert test_client.get("/health").status_code == 200
        assert app.state.cleanup_task is None
    assert old_upload.exists()


def test_enabled_background_cleanup_stops_gracefully(
    storage_layout, monkeypatch
):
    monkeypatch.setattr(settings, "cleanup_enabled", True)
    cleanup_started = threading.Event()

    def fake_cleanup():
        cleanup_started.set()
        return {"uploads": 0, "reports": 0}

    with patch("main.cleanup_old_files", side_effect=fake_cleanup):
        with TestClient(app) as test_client:
            assert test_client.get("/health").status_code == 200
            task = app.state.cleanup_task
            assert task is not None
            assert cleanup_started.wait(timeout=2)

    assert task.done()


def test_enabled_background_cleanup_removes_expired_artifacts(
    storage_layout, monkeypatch
):
    from core.cleanup import cleanup_old_files as run_cleanup

    _, uploads, reports = storage_layout
    old_upload = uploads / f"{uuid.uuid4()}.csv"
    old_report = reports / f"{uuid.uuid4()}.md"
    _age_file(old_upload)
    _age_file(old_report)
    monkeypatch.setattr(settings, "cleanup_enabled", True)
    cleanup_finished = threading.Event()

    def cleanup_and_signal():
        result = run_cleanup()
        cleanup_finished.set()
        return result

    with patch("main.cleanup_old_files", side_effect=cleanup_and_signal):
        with TestClient(app) as test_client:
            assert test_client.get("/health").status_code == 200
            assert cleanup_finished.wait(timeout=2)

    assert not old_upload.exists()
    assert not old_report.exists()


def test_cleanup_environment_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOWPILOT_STORAGE_ROOT", str(tmp_path / "config-storage"))
    monkeypatch.delenv("FLOWPILOT_CLEANUP_ENABLED", raising=False)
    monkeypatch.delenv("FLOWPILOT_UPLOAD_RETENTION_HOURS", raising=False)
    monkeypatch.delenv("FLOWPILOT_REPORT_RETENTION_HOURS", raising=False)
    defaults = Settings()
    assert defaults.cleanup_enabled is False
    assert defaults.upload_retention_hours == 24
    assert defaults.report_retention_hours == 24

    monkeypatch.setenv("FLOWPILOT_CLEANUP_ENABLED", "true")
    monkeypatch.setenv("FLOWPILOT_UPLOAD_RETENTION_HOURS", "12")
    monkeypatch.setenv("FLOWPILOT_REPORT_RETENTION_HOURS", "36")
    configured = Settings()
    assert configured.cleanup_enabled is True
    assert configured.upload_retention_hours == 12
    assert configured.report_retention_hours == 36


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="Symlinks are unavailable")
def test_cleanup_does_not_follow_symlinked_artifact(storage_layout, tmp_path):
    _, uploads, _ = storage_layout
    outside_file = tmp_path / "external.csv"
    _age_file(outside_file)
    linked_file = uploads / f"{uuid.uuid4()}.csv"
    try:
        linked_file.symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlink creation is not permitted")

    cleanup_old_files()

    assert linked_file.is_symlink()
    assert outside_file.exists()
