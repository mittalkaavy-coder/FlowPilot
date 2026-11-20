from typing import Optional, Dict, Any, List
from core.security import resolve_report_path


def run(
    report_id: Optional[str] = None,
    expected_requirements: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Verify report artifact integrity and requirement-aware completeness.

    Phases:
    1. File Integrity: verifies file existence, non-zero size, and valid UTF-8 readability.
    2. Content Completeness & Value Accuracy: deterministically verifies that all expected
       metrics, correlation values/interpretations, and top-performing entities exist in the report.

    Args:
        report_id: UUID of the report file.
        expected_requirements: Optional dict specifying expected analysis metrics, correlation,
                               top performer, and user goal.

    Returns:
        dict with verification verdict, integrity breakdown, completeness breakdown, and details.
    """
    if not report_id:
        return {
            "verified": False,
            "reason": "No report_id provided",
            "integrity": {"passed": False, "reason": "No report_id provided"},
            "completeness": {"passed": False, "reason": "No report_id provided"},
        }

    try:
        path = resolve_report_path(report_id)
    except Exception as e:
        return {
            "verified": False,
            "report_id": report_id,
            "reason": f"Path resolution error: {e}",
            "integrity": {"passed": False, "reason": str(e)},
            "completeness": {"passed": False},
        }

    # ── Phase 1: File Integrity ──────────────────────────────────────────────
    if not path.exists():
        return {
            "verified": False,
            "report_id": report_id,
            "reason": "Report file does not exist",
            "integrity": {"passed": False, "exists": False},
            "completeness": {"passed": False},
        }

    size_bytes = path.stat().st_size
    if size_bytes == 0:
        return {
            "verified": False,
            "report_id": report_id,
            "reason": "Report file is empty",
            "integrity": {"passed": False, "exists": True, "size_bytes": 0},
            "completeness": {"passed": False},
        }

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        return {
            "verified": False,
            "report_id": report_id,
            "reason": f"Report file cannot be decoded as UTF-8: {exc}",
            "integrity": {
                "passed": False,
                "exists": True,
                "size_bytes": size_bytes,
                "readable_utf8": False,
            },
            "completeness": {"passed": False},
        }

    has_tables = "|" in content
    lines = content.splitlines()

    integrity_verdict = {
        "passed": True,
        "exists": True,
        "size_bytes": size_bytes,
        "readable_utf8": True,
        "has_tables": has_tables,
        "line_count": len(lines),
        "word_count": len(content.split()),
    }

    # ── Phase 2: Requirement & Value Completeness ────────────────────────────
    completeness_verdict = {
        "passed": True,
        "checked_requirements": [],
        "missing": [],
    }
    missing_elements: List[str] = []

    if expected_requirements:
        # Check table presence if structured reporting is expected
        if expected_requirements.get("require_tables", True) and not has_tables:
            missing_elements.append("Report missing formatted markdown tables")

        # Check dataset structure & data types
        if expected_requirements.get("require_dataset_structure"):
            completeness_verdict["checked_requirements"].append("dataset_structure")
            has_structure = (
                "dataset structure" in content.lower()
                or "data types" in content.lower()
                or "column name" in content.lower()
            )
            if not has_structure:
                missing_elements.append("Missing Dataset Structure & Data Types section")

        # Check missing values & data quality
        if expected_requirements.get("require_missing_values"):
            completeness_verdict["checked_requirements"].append("missing_values")
            has_missing = (
                "missing value" in content.lower()
                or "null count" in content.lower()
                or "data completeness" in content.lower()
                or "data quality" in content.lower()
            )
            if not has_missing:
                missing_elements.append("Missing Missing Values / Data Quality assessment")

        # Check descriptive / summary statistics
        if expected_requirements.get("require_statistics"):
            completeness_verdict["checked_requirements"].append("descriptive_statistics")
            has_stats = (
                ("descriptive statistics" in content.lower() or "summary statistics" in content.lower())
                and ("mean" in content.lower() or "average" in content.lower())
            )
            if not has_stats:
                missing_elements.append("Missing Descriptive Statistics / Summary Statistics section")

        # Check required metrics
        metrics = expected_requirements.get("metrics") or []
        for metric in metrics:
            completeness_verdict["checked_requirements"].append(f"metric:{metric}")
            if metric.lower() not in content.lower():
                missing_elements.append(f"Missing required metric '{metric}'")

        # Check top performer (name and value)
        top_performer = expected_requirements.get("top_performer")
        if top_performer:
            top_name = str(top_performer.get("name", ""))
            top_val = top_performer.get("value")
            if top_name:
                completeness_verdict["checked_requirements"].append(
                    f"top_performer_name:{top_name}"
                )
                if top_name.lower() not in content.lower():
                    missing_elements.append(f"Missing top performer name '{top_name}'")
            if top_val is not None:
                completeness_verdict["checked_requirements"].append(
                    f"top_performer_value:{top_val}"
                )
                val_str1 = f"{float(top_val):,.2f}"
                val_str2 = (
                    str(int(top_val))
                    if float(top_val).is_integer()
                    else str(top_val)
                )
                if val_str1 not in content and val_str2 not in content:
                    missing_elements.append(
                        f"Missing top performer value '{val_str1}'"
                    )

        # Check correlation requirements
        if expected_requirements.get("require_correlation"):
            completeness_verdict["checked_requirements"].append("correlation_section")
            if (
                "correlation analysis" not in content.lower()
                and "pearson" not in content.lower()
            ):
                missing_elements.append(
                    "Missing Correlation Analysis section or Pearson metrics"
                )

            corr_cols = expected_requirements.get("correlation_columns") or []
            for col in corr_cols:
                completeness_verdict["checked_requirements"].append(
                    f"correlation_column:{col}"
                )
                if col.lower() not in content.lower():
                    missing_elements.append(
                        f"Correlation column '{col}' not mentioned in report"
                    )

            expected_coef = expected_requirements.get("correlation_coefficient")
            if expected_coef is not None:
                completeness_verdict["checked_requirements"].append(
                    f"correlation_coefficient:{expected_coef}"
                )
                coef_str = f"{float(expected_coef):.4f}"
                if coef_str not in content and coef_str[:5] not in content:
                    missing_elements.append(
                        f"Missing expected correlation coefficient '{coef_str}'"
                    )

            expected_interp = expected_requirements.get("correlation_interpretation")
            if expected_interp:
                completeness_verdict["checked_requirements"].append(
                    f"correlation_interpretation:{expected_interp}"
                )
                if expected_interp.lower() not in content.lower():
                    missing_elements.append(
                        f"Missing correlation interpretation '{expected_interp}'"
                    )

    if missing_elements:
        completeness_verdict["passed"] = False
        completeness_verdict["missing"] = missing_elements
        return {
            "verified": False,
            "report_id": report_id,
            "size_bytes": size_bytes,
            "line_count": len(lines),
            "word_count": len(content.split()),
            "reason": f"Report failed requirement completeness: {'; '.join(missing_elements)}",
            "integrity": integrity_verdict,
            "completeness": completeness_verdict,
            "preview": content[:400],
            "has_tables": has_tables,
        }

    return {
        "verified": True,
        "report_id": report_id,
        "size_bytes": size_bytes,
        "line_count": len(lines),
        "word_count": len(content.split()),
        "integrity": integrity_verdict,
        "completeness": completeness_verdict,
        "preview": content[:400],
        "has_tables": has_tables,
    }
