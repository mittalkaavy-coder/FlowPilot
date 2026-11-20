"""Tests for file security, path traversal prevention, content validation, and cleanup."""
import io
import time
import pytest
from pathlib import Path
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from core.security import (
    validate_uuid,
    resolve_upload_path,
    resolve_report_path,
    validate_upload,
    validate_csv_content,
)
from core.cleanup import cleanup_old_files

client = TestClient(app)


def test_validate_uuid_valid():
    # Valid UUIDv4
    validate_uuid("12345678-1234-4234-8234-123456789abc")


def test_validate_uuid_invalid_format():
    with pytest.raises(HTTPException) as exc_info:
        validate_uuid("not-a-real-uuid")
    assert exc_info.value.status_code == 400


def test_path_traversal_in_upload_path_blocked(tmp_path):
    with pytest.raises(HTTPException) as exc_info:
        resolve_upload_path("../etc/passwd")
    assert exc_info.value.status_code == 400


def test_path_traversal_in_download_endpoint():
    # Attempt directory traversal via download route
    r = client.get("/api/download/../../etc/passwd")
    # Will either match 400 (invalid UUID) or 404
    assert r.status_code in [400, 404]


def test_csv_content_validation_empty_fails():
    with pytest.raises(HTTPException) as exc:
        validate_csv_content(b"")
    assert exc.value.status_code == 422
    assert "empty" in exc.value.detail.lower()


def test_csv_content_validation_whitespace_fails():
    with pytest.raises(HTTPException) as exc:
        validate_csv_content(b"   \n\n  \t  ")
    assert exc.value.status_code == 422


def test_csv_content_validation_non_utf8_binary_fails():
    # Random invalid UTF-8 bytes like JPEG/ELF header
    invalid_bytes = b"\xff\xfe\x00\x00\x80\x90\xaa\xbb"
    with pytest.raises(HTTPException) as exc:
        validate_csv_content(invalid_bytes)
    assert exc.value.status_code == 422
    assert "utf-8" in exc.value.detail.lower()


def test_csv_content_validation_valid():
    valid = b"name,amount,category\nItem A,10.5,Retail\nItem B,20.0,Services"
    # Should not raise
    validate_csv_content(valid)


def test_upload_validation_filename_too_long():
    long_name = "a" * 300 + ".csv"
    with pytest.raises(HTTPException) as exc:
        validate_upload(long_name, 100)
    assert exc.value.status_code == 400
    assert "too long" in exc.value.detail.lower()


def test_upload_validation_wrong_extension():
    with pytest.raises(HTTPException) as exc:
        validate_upload("payload.exe", 100)
    assert exc.value.status_code == 415


def test_cleanup_old_files(tmp_path, monkeypatch):
    up_dir = tmp_path / "uploads"
    rep_dir = tmp_path / "reports"
    up_dir.mkdir(exist_ok=True)
    rep_dir.mkdir(exist_ok=True)

    # Create an old file and a fresh file
    old_file = up_dir / "old.csv"
    old_file.write_text("old")
    fresh_file = up_dir / "fresh.csv"
    fresh_file.write_text("fresh")

    # Set old_file mtime to 48 hours ago
    two_days_ago = time.time() - (48 * 3600)
    import os
    os.utime(old_file, (two_days_ago, two_days_ago))

    from core.config import settings
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", up_dir)
    monkeypatch.setattr(settings, "report_dir", rep_dir)

    res = cleanup_old_files(max_age_hours=24)
    assert res["uploads"] == 1
    assert not old_file.exists()
    assert fresh_file.exists()
