import uuid
import pytest
from pathlib import Path
from unittest.mock import patch
from core.store import clear_store
from core.config import settings

TEST_CSV_CONTENT = """Product,Sales,Date,Region
Widget A,1500.00,2024-01-15,North
Widget B,2300.00,2024-01-20,South
Widget C,800.00,2024-02-10,North
Widget A,1200.00,2024-02-15,South
Widget B,1900.00,2024-03-05,North
Widget C,650.00,2024-03-20,South
Widget A,2100.00,2024-03-25,North
"""

VALID_TEST_UUID = "a1b2c3d4-e5f6-4a1b-8c2d-3e4f5a6b7c8d"


@pytest.fixture(autouse=True)
def isolated_test_db(tmp_path):
    """Use an isolated temporary SQLite database for every test."""
    orig_db = settings.db_path
    test_db = tmp_path / f"test_fp_{uuid.uuid4().hex[:8]}.db"
    settings.db_path = test_db
    clear_store()
    yield test_db
    clear_store()
    settings.db_path = orig_db


@pytest.fixture
def sample_csv(tmp_path):
    """Create a temporary CSV file."""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text(TEST_CSV_CONTENT)
    return csv_file


@pytest.fixture
def patched_settings(tmp_path, sample_csv, isolated_test_db):
    """Patch settings to use temporary directories with a valid seeded CSV and isolated DB."""
    upload_dir = tmp_path / "uploads"
    report_dir = tmp_path / "reports"
    upload_dir.mkdir(exist_ok=True)
    report_dir.mkdir(exist_ok=True)

    file_id = VALID_TEST_UUID
    (upload_dir / f"{file_id}.csv").write_text(TEST_CSV_CONTENT)

    with patch("core.config.settings") as mock_settings:
        mock_settings.upload_dir = upload_dir
        mock_settings.report_dir = report_dir
        mock_settings.db_path = isolated_test_db
        mock_settings.max_upload_bytes = 10 * 1024 * 1024
        mock_settings.gemini_api_key = "test-api-key"
        mock_settings.gemini_model = "gemini-3.8-flash"
        mock_settings.gemini_fallback_model = "gemini-3.1-flash-lite"
        mock_settings.allowed_origins = ["*"]
        yield mock_settings, file_id, upload_dir, report_dir
