"""Streaming upload size enforcement tests for the upload endpoint."""
import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app


client = TestClient(app)
LIMIT = 64
BOUNDARY = "flowpilot-test-boundary"


def _csv_of_size(size: int) -> bytes:
    prefix = b"name,value\n"
    suffix = b",1\n"
    padding_size = size - len(prefix) - len(suffix)
    assert padding_size >= 1
    return prefix + (b"x" * padding_size) + suffix


def _multipart_body(file_content: bytes) -> bytes:
    return (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="upload.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode() + file_content + f"\r\n--{BOUNDARY}--\r\n".encode()


async def _chunked_body(body: bytes):
    for offset in range(0, len(body), 4096):
        yield body[offset:offset + 4096]


@pytest.fixture
def upload_settings(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr("core.config.settings.upload_dir", upload_dir)
    monkeypatch.setattr("core.config.settings.max_upload_bytes", LIMIT)
    return upload_dir


@pytest.mark.parametrize("size", [40, LIMIT])
def test_upload_accepts_csv_below_and_exactly_at_limit(upload_settings, size):
    with patch("api.routes.files.inspect_csv", return_value={
        "shape": {"rows": 1, "columns": 2},
        "columns": [{"name": "name"}, {"name": "value"}],
    }):
        response = client.post(
            "/api/upload",
            files={"file": ("valid.csv", _csv_of_size(size), "text/csv")},
        )

    assert response.status_code == 200
    assert set(response.json()) == {"file_id", "filename", "rows", "columns", "column_names"}
    assert response.json()["rows"] == 1
    assert len(list(upload_settings.glob("*.csv"))) == 1


@pytest.mark.asyncio
async def test_chunked_oversized_upload_is_rejected_without_persistent_file(upload_settings):
    body = _multipart_body(_csv_of_size(LIMIT + 1))
    with patch("api.routes.files.inspect_csv") as inspect_csv:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as async_client:
            response = await async_client.post(
                "/api/upload",
                content=_chunked_body(body),
                headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
            )

    assert response.status_code == 413
    assert "maximum allowed size" in response.json()["detail"].lower()
    inspect_csv.assert_not_called()
    assert list(upload_settings.iterdir()) == []


@pytest.mark.asyncio
async def test_oversized_upload_is_rejected_with_misleading_content_length(upload_settings):
    body = _multipart_body(_csv_of_size(LIMIT + 1))
    with patch("api.routes.files.inspect_csv") as inspect_csv:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as async_client:
            response = await async_client.post(
                "/api/upload",
                content=_chunked_body(body),
                headers={
                    "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
                    "Content-Length": "1",
                },
            )

    assert response.status_code == 413
    inspect_csv.assert_not_called()
    assert list(upload_settings.iterdir()) == []


def test_non_upload_api_endpoints_remain_available():
    assert client.get("/health").status_code == 200
    missing_report = client.get(f"/api/download/{uuid.uuid4()}")
    assert missing_report.status_code == 404
