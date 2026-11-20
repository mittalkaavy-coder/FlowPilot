"""Tests for the 4 allowlisted tools."""
import uuid
import math
import pytest
from pathlib import Path

from tools.csv_inspector import run as inspect_run
from tools.csv_analyzer import run as analyze_run
from tools.report_generator import run as report_run
from tools.artifact_verifier import run as verify_run


_STUDENT_PERFORMANCE_CSV = """Name,Study_Hours,Attendance,Marks
Aman,5,90,85
Riya,3,75,68
Karan,7,95,92
Neha,2,65,55
Rahul,6,85,88
Priya,4,80,76
"""


# ─── csv_inspector ────────────────────────────────────────────────────────────

class TestCsvInspector:
    def test_csv_inspector_returns_shape(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        result = inspect_run(file_id)
        assert result["shape"]["rows"] == 7
        assert result["shape"]["columns"] == 4

    def test_csv_inspector_column_types(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        result = inspect_run(file_id)
        assert "Sales" in result["numeric_columns"]
        assert "Date" in result["date_columns"]
        assert "Product" in result["category_columns"]

    def test_csv_inspector_sample_rows(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        result = inspect_run(file_id)
        assert len(result["sample_rows"]) > 0


# ─── csv_analyzer ────────────────────────────────────────────────────────────

class TestCsvAnalyzer:
    def test_csv_analyzer_statistics(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        result = analyze_run(file_id, value_column="Sales")
        stats = result["statistics"]
        # 1500+2300+800+1200+1900+650+2100 = 10450
        assert stats["total"] == 10450.0
        assert stats["count"] == 7
        assert stats["min"] == 650.0
        assert stats["max"] == 2300.0

    def test_csv_analyzer_top_items(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        result = analyze_run(
            file_id,
            value_column="Sales",
            category_column="Product",
            top_n=3,
        )
        assert "top_items" in result
        items = result["top_items"]
        assert len(items) == 3
        # Widget A total = 1500+1200+2100 = 4800
        # Widget B total = 2300+1900 = 4200
        # Widget C total = 800+650 = 1450
        names = [i["name"] for i in items]
        assert names[0] == "Widget A"
        assert names[1] == "Widget B"

    def test_csv_analyzer_monthly_trends(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        result = analyze_run(
            file_id,
            value_column="Sales",
            date_column="Date",
        )
        assert "monthly_trends" in result
        months = [m["month"] for m in result["monthly_trends"]]
        assert "2024-01" in months
        assert "2024-02" in months
        assert "2024-03" in months

    def test_csv_analyzer_invalid_column(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        with pytest.raises(ValueError, match="not found"):
            analyze_run(file_id, value_column="NonExistentColumn")

    def test_pearson_correlation_student_performance(self, patched_settings):
        """Phase 2: Pearson correlation between Study_Hours and Marks should be ~0.9721."""
        mock_settings, _, upload_dir, _ = patched_settings
        sp_id = str(uuid.uuid4())
        (upload_dir / f"{sp_id}.csv").write_text(_STUDENT_PERFORMANCE_CSV, encoding="utf-8")

        result = analyze_run(
            sp_id,
            value_column="Marks",
            correlation_column="Study_Hours",
        )
        assert "correlation" in result
        corr = result["correlation"]
        assert corr["column_1"] == "Marks"
        assert corr["column_2"] == "Study_Hours"
        assert corr["sample_size"] == 6
        # Requirement 1: Expect ~0.9721 for student_performance.csv
        assert abs(corr["coefficient"] - 0.9721) < 0.001
        assert corr["interpretation"] == "Strong positive linear correlation"

    def test_pearson_correlation_missing_and_constant_columns(self, patched_settings):
        """Phase 2: Safely handles constant columns (zero variance) and missing values."""
        mock_settings, _, upload_dir, _ = patched_settings

        # 1. Constant column (std == 0)
        const_id = str(uuid.uuid4())
        const_csv = "A,B\n5,10\n5,20\n5,30\n"
        (upload_dir / f"{const_id}.csv").write_text(const_csv, encoding="utf-8")

        res_const = analyze_run(const_id, value_column="B", correlation_column="A")
        assert res_const["correlation"]["coefficient"] is None
        assert "zero variance" in res_const["correlation"]["interpretation"]

        # 2. Missing values dropped safely
        na_id = str(uuid.uuid4())
        na_csv = "X,Y\n1,2\n2,4\nNaN,6\n4,NaN\n5,10\n"
        (upload_dir / f"{na_id}.csv").write_text(na_csv, encoding="utf-8")

        res_na = analyze_run(na_id, value_column="Y", correlation_column="X")
        assert res_na["correlation"]["sample_size"] == 3
        assert res_na["correlation"]["coefficient"] == 1.0
        assert res_na["correlation"]["interpretation"] == "Strong positive linear correlation"

    def test_pearson_correlation_invalid_column(self, patched_settings):
        mock_settings, file_id, upload_dir, _ = patched_settings
        with pytest.raises(ValueError, match="Correlation column 'MissingCol' not found"):
            analyze_run(file_id, value_column="Sales", correlation_column="MissingCol")


# ─── report_generator ────────────────────────────────────────────────────────

class TestReportGenerator:
    _analysis = {
        "statistics": {
            "total": 10450.0,
            "mean": 1492.86,
            "median": 1500.0,
            "std": 578.0,
            "min": 650.0,
            "max": 2300.0,
            "count": 7,
        },
        "top_items": [
            {"rank": 1, "name": "Widget A", "value": 4800.0, "share_pct": 45.9},
            {"rank": 2, "name": "Widget B", "value": 4200.0, "share_pct": 40.2},
            {"rank": 3, "name": "Widget C", "value": 1450.0, "share_pct": 13.9},
        ],
        "monthly_trends": [
            {"month": "2024-01", "value": 3800.0},
            {"month": "2024-02", "value": 2000.0},
            {"month": "2024-03", "value": 4650.0},
        ],
        "correlation": {
            "column_1": "Marks",
            "column_2": "Study_Hours",
            "coefficient": 0.9721,
            "sample_size": 6,
            "interpretation": "Strong positive linear correlation",
        },
        "value_column": "Sales",
        "category_column": "Product",
        "date_column": "Date",
    }

    def test_report_generator_creates_file(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        result = report_run(
            file_id=file_id,
            title="Test Report",
            analysis_results=self._analysis,
        )
        assert "report_id" in result
        report_path = report_dir / f"{result['report_id']}.md"
        assert report_path.exists()
        assert result["size_bytes"] > 0

    def test_report_generator_has_sections(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        result = report_run(
            file_id=file_id,
            title="Test Report",
            analysis_results=self._analysis,
        )
        report_path = report_dir / f"{result['report_id']}.md"
        content = report_path.read_text(encoding="utf-8")
        assert "## Summary Statistics" in content
        assert "## Top Items" in content
        assert "## Monthly Trends" in content
        assert "## Correlation Analysis" in content
        assert "Pearson Coefficient (r)" in content
        assert "0.9721" in content
        assert "|" in content  # tables present


# ─── artifact_verifier ───────────────────────────────────────────────────────

class TestArtifactVerifier:
    def test_artifact_verifier_success(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        report_id = str(uuid.uuid4())
        report_file = report_dir / f"{report_id}.md"
        report_file.write_text("# Test Report\n\nContent here.\n", encoding="utf-8")

        result = verify_run(report_id=report_id)
        assert result["verified"] is True
        assert result["size_bytes"] > 0
        assert result["line_count"] > 0
        assert result["integrity"]["passed"] is True

    def test_artifact_verifier_missing_file(self, patched_settings):
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        missing_id = str(uuid.uuid4())
        result = verify_run(report_id=missing_id)
        assert result["verified"] is False
        assert "does not exist" in result["reason"]

    def test_artifact_verifier_fails_on_missing_requirements(self, patched_settings):
        """Phase 2: Deterministically fails when expected requirements are missing from content."""
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        report_id = str(uuid.uuid4())
        report_file = report_dir / f"{report_id}.md"
        # Incomplete report: missing correlation section and top performer
        report_file.write_text(
            "# Incomplete Report\n\n| Col | Val |\n|---|---|\n| A | 1 |\n",
            encoding="utf-8",
        )

        expected = {
            "metrics": ["Marks"],
            "top_performer": {"name": "Karan", "value": 92.0},
            "require_correlation": True,
            "correlation_columns": ["Study_Hours", "Marks"],
            "correlation_coefficient": 0.9721,
            "correlation_interpretation": "Strong positive linear correlation",
        }
        result = verify_run(report_id=report_id, expected_requirements=expected)
        assert result["verified"] is False
        assert result["integrity"]["passed"] is True  # File itself is valid UTF-8 and exists
        assert result["completeness"]["passed"] is False
        assert any("Missing required metric 'Marks'" in m for m in result["completeness"]["missing"])
        assert any("Missing top performer name 'Karan'" in m for m in result["completeness"]["missing"])
        assert any("Missing Correlation Analysis" in m for m in result["completeness"]["missing"])

    def test_artifact_verifier_succeeds_on_all_requirements(self, patched_settings):
        """Phase 2: Passes when all metrics, correlation, and top performer match."""
        mock_settings, file_id, upload_dir, report_dir = patched_settings
        report_id = str(uuid.uuid4())
        report_file = report_dir / f"{report_id}.md"

        full_content = """# Complete Student Report

## Summary Statistics
| Metric | Value |
|--------|------:|
| Total Marks | 464.00 |

## Top Items by Marks
| Rank | Name | Marks | Share % |
|-----:|------|------:|--------:|
| 1 | Karan | 92.00 | 19.8% |

## Correlation Analysis
| Metric | Value |
|--------|------:|
| Variables | Marks & Study_Hours |
| Pearson Coefficient (r) | 0.9721 |
| Interpretation | Strong positive linear correlation |

## Key Insights
- Karan is top performer with 92.00.
"""
        report_file.write_text(full_content, encoding="utf-8")

        expected = {
            "metrics": ["Marks"],
            "top_performer": {"name": "Karan", "value": 92.0},
            "require_correlation": True,
            "correlation_columns": ["Study_Hours", "Marks"],
            "correlation_coefficient": 0.9721,
            "correlation_interpretation": "Strong positive linear correlation",
        }
        result = verify_run(report_id=report_id, expected_requirements=expected)
        assert result["verified"] is True
        assert result["integrity"]["passed"] is True
        assert result["completeness"]["passed"] is True
        assert len(result["completeness"]["missing"]) == 0
