import uuid
from datetime import datetime, timezone
from pathlib import Path

from core import config
from core.security import resolve_upload_path


def run(
    file_id: str,
    title: str,
    analysis_results: dict = None,
    inspection_results: dict = None,
    report_id: str = None,
) -> dict:
    """
    Generate a comprehensive Markdown report from analysis and inspection results.

    Args:
        file_id: UUID of the uploaded CSV.
        title: Report title.
        analysis_results: Output from csv_analyzer (injected by executor). May be None.
        inspection_results: Output from csv_inspector (injected by executor or auto-loaded).
        report_id: Optional report UUID. Generated automatically if not provided.

    Returns:
        dict with report_id, path, size_bytes, line_count, preview.
    """
    if not report_id:
        report_id = str(uuid.uuid4())

    # Auto-load inspection results if not provided
    if inspection_results is None:
        try:
            from tools.csv_inspector import run as inspect_csv
            inspection_results = inspect_csv(file_id)
        except Exception:
            inspection_results = None

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = []

    # ── Title & metadata ────────────────────────────────────────────────────
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"**Generated:** {now}  ")
    lines.append(f"**File ID:** `{file_id}`  ")
    lines.append(f"**Report ID:** `{report_id}`  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Executive Summary ───────────────────────────────────────────────────
    lines.append("## Executive Summary")
    lines.append("")
    if analysis_results:
        stats = analysis_results.get("statistics", {})
        value_col = analysis_results.get("value_column", "value")
        total = stats.get("total", 0)
        count = stats.get("count", 0)
        mean = stats.get("mean", 0)
        lines.append(
            f"This report analyses **{count}** records with a total **{value_col}** "
            f"of **{total:,.2f}** and an average of **{mean:,.2f}** per record."
        )
    else:
        lines.append(
            "This report summarises the uploaded dataset. "
            "No detailed analysis results were provided."
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Dataset Structure & Data Types ─────────────────────────────────────
    if inspection_results:
        shape = inspection_results.get("shape", {})
        rows_cnt = shape.get("rows", 0)
        cols_cnt = shape.get("columns", 0)
        lines.append("## Dataset Structure & Data Types")
        lines.append("")
        lines.append(
            f"The dataset contains **{rows_cnt}** rows and **{cols_cnt}** columns. "
            "Below is the complete dataset structure, column data types, missing value counts, and sample values:"
        )
        lines.append("")
        lines.append("| Column Name | Data Type | Missing Values | Unique Count | Sample Values |")
        lines.append("|-------------|-----------|---------------:|-------------:|---------------|")
        columns = inspection_results.get("columns", [])
        for col in columns:
            name = col.get("name", "")
            dtype = col.get("dtype", "")
            null_count = col.get("null_count", 0)
            unique_count = col.get("unique_count", 0)
            sample_vals = ", ".join(str(v) for v in col.get("sample_values", [])[:3])
            lines.append(
                f"| `{name}` | `{dtype}` | {null_count:,} | {unique_count:,} | {sample_vals} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

        # ── Missing Values & Data Quality Assessment ─────────────────────────
        lines.append("## Missing Values & Data Quality Assessment")
        lines.append("")
        total_nulls = sum(col.get("null_count", 0) for col in columns)
        if total_nulls == 0:
            lines.append(
                "**Data Completeness:** Zero missing values detected across all columns (100% complete dataset). "
                "All records are valid for statistical evaluation."
            )
        else:
            lines.append(
                f"**Data Completeness Warning:** A total of **{total_nulls}** missing value(s) were identified:"
            )
            for col in columns:
                if col.get("null_count", 0) > 0:
                    lines.append(f"- Column `{col['name']}`: {col['null_count']} missing value(s)")
        lines.append("")
        lines.append("---")
        lines.append("")

    if analysis_results:
        stats = analysis_results.get("statistics", {})
        value_col = analysis_results.get("value_column", "value")

        # ── Summary Statistics & Descriptive Statistics ──────────────────────
        if stats:
            lines.append("## Summary Statistics")
            lines.append("")
            lines.append(
                f"Descriptive statistical summary for primary numeric column **{value_col}**:"
            )
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|------:|")
            lines.append(f"| Total | {stats.get('total', 0):,.2f} |")
            lines.append(f"| Mean | {stats.get('mean', 0):,.2f} |")
            lines.append(f"| Median | {stats.get('median', 0):,.2f} |")
            lines.append(f"| Std Dev | {stats.get('std', 0):,.2f} |")
            lines.append(f"| Min | {stats.get('min', 0):,.2f} |")
            lines.append(f"| Max | {stats.get('max', 0):,.2f} |")
            lines.append(f"| Count | {stats.get('count', 0):,} |")
            lines.append("")
            lines.append("---")
            lines.append("")

        # ── Top-Performing Analysis & Top Items ──────────────────────────────
        top_items = analysis_results.get("top_items")
        category_col = analysis_results.get("category_column") or "Category"
        if top_items:
            lines.append(f"## Top Items by {value_col.capitalize()}")
            lines.append("")
            top_p = top_items[0]
            lines.append(
                f"**Top Performer:** **{top_p['name']}** is the top-performing entity with "
                f"**{top_p['value']:,.2f}** {value_col} ({top_p.get('share_pct', 0):.1f}% share of total)."
            )
            lines.append("")
            lines.append(f"| Rank | {category_col} | {value_col.capitalize()} | Share % |")
            lines.append("|-----:|---------|------:|-------:|")
            for item in top_items:
                lines.append(
                    f"| {item['rank']} | {item['name']} "
                    f"| {item['value']:,.2f} | {item.get('share_pct', 0):.1f}% |"
                )
            lines.append("")
            lines.append("---")
            lines.append("")

        # ── Monthly Trends ───────────────────────────────────────────────────
        monthly = analysis_results.get("monthly_trends")
        if monthly:
            lines.append("## Monthly Trends")
            lines.append("")
            lines.append(f"| Month | {value_col.capitalize()} |")
            lines.append("|-------|------:|")
            for entry in monthly:
                lines.append(f"| {entry['month']} | {entry['value']:,.2f} |")
            lines.append("")
            lines.append("---")
            lines.append("")

        # ── Correlation Analysis ─────────────────────────────────────────────
        corr = analysis_results.get("correlation")
        if corr:
            lines.append("## Correlation Analysis")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|------:|")
            lines.append(f"| Variables | {corr.get('column_1')} & {corr.get('column_2')} |")
            r_coef = corr.get("coefficient")
            r_str = f"{r_coef:.4f}" if r_coef is not None else "N/A"
            lines.append(f"| Pearson Coefficient (r) | {r_str} |")
            lines.append(f"| Sample Size | {corr.get('sample_size', 0)} |")
            lines.append(f"| Interpretation | {corr.get('interpretation', 'N/A')} |")
            lines.append("")
            lines.append("---")
            lines.append("")

        # ── Key Insights ─────────────────────────────────────────────────────
        lines.append("## Key Insights")
        lines.append("")
        insights: list[str] = []

        if top_items:
            top = top_items[0]
            insights.append(
                f"- **{top['name']}** is the leading item, accounting for "
                f"**{top.get('share_pct', 0):.1f}%** of total {value_col}."
            )
            if len(top_items) >= 2:
                second = top_items[1]
                insights.append(
                    f"- **{second['name']}** ranks second with "
                    f"**{second['value']:,.2f}** ({second.get('share_pct', 0):.1f}%)."
                )

        if corr:
            r_coef = corr.get("coefficient")
            c1, c2 = corr.get("column_1"), corr.get("column_2")
            interp = corr.get("interpretation")
            if r_coef is not None:
                insights.append(
                    f"- **Correlation Analysis:** A **{interp.lower()}** (r = {r_coef:.4f}) was observed "
                    f"between **{c1}** and **{c2}** across {corr.get('sample_size', 0)} records."
                )
            else:
                insights.append(
                    f"- **Correlation Analysis:** Correlation between **{c1}** and **{c2}** could not be "
                    f"computed due to constant values or zero variance."
                )

        if monthly:
            best = max(monthly, key=lambda x: x["value"])
            worst = min(monthly, key=lambda x: x["value"])
            insights.append(
                f"- The best month was **{best['month']}** with "
                f"**{best['value']:,.2f}** in {value_col}."
            )
            insights.append(
                f"- The slowest month was **{worst['month']}** with "
                f"**{worst['value']:,.2f}** in {value_col}."
            )

        if stats:
            cv = (stats.get("std", 0) / stats.get("mean", 1)) * 100 if stats.get("mean") else 0
            if cv > 30:
                insights.append(
                    f"- High variability detected: coefficient of variation is "
                    f"**{cv:.1f}%**, suggesting uneven distribution."
                )
            else:
                insights.append(
                    f"- {value_col.capitalize()} is relatively consistent "
                    f"(CV = {cv:.1f}%)."
                )

        if insights:
            lines.extend(insights)
        else:
            lines.append("_No specific insights could be generated from the available data._")

        lines.append("")

    else:
        lines.append("## Notes")
        lines.append("")
        lines.append("_Analysis results were not available at report generation time._")
        lines.append("")

    # ── Footer ───────────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("*Report generated by FlowPilot — Agentic Workflow Automation*")

    content = "\n".join(lines)

    # Save to disk
    report_path = config.settings.report_dir / f"{report_id}.md"
    report_path.write_text(content, encoding="utf-8")

    return {
        "report_id": report_id,
        "path": str(report_path),
        "size_bytes": report_path.stat().st_size,
        "line_count": len(lines),
        "preview": content[:400],
    }
