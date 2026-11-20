import pandas as pd
import math
from core.security import resolve_upload_path


def _safe_float(v) -> float:
    """Convert to float, replacing NaN/inf with 0."""
    try:
        f = float(v)
        return 0.0 if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return 0.0


def run(
    file_id: str,
    value_column: str,
    category_column: str = None,
    date_column: str = None,
    top_n: int = 3,
    correlation_column: str = None,
) -> dict:
    """
    Analyze a CSV: summary statistics, top-N by category, monthly trends,
    and Pearson correlation between two numeric columns.
    """
    path = resolve_upload_path(file_id)
    df = pd.read_csv(path)

    if value_column not in df.columns:
        raise ValueError(
            f"Column '{value_column}' not found. Available: {list(df.columns)}"
        )

    series = pd.to_numeric(df[value_column], errors="coerce")

    result: dict = {}

    # Summary statistics
    result["statistics"] = {
        "total": _safe_float(series.sum()),
        "mean": _safe_float(series.mean()),
        "median": _safe_float(series.median()),
        "std": _safe_float(series.std()),
        "min": _safe_float(series.min()),
        "max": _safe_float(series.max()),
        "count": int(series.count()),
    }

    # Top N by category (with automatic detection if not explicitly passed)
    active_category_col = category_column
    if not active_category_col or active_category_col not in df.columns:
        cat_candidates = df.select_dtypes(include=["object", "category", "string"]).columns.tolist()
        if cat_candidates:
            active_category_col = cat_candidates[0]

    if active_category_col and active_category_col in df.columns:
        grouped = (
            df.groupby(active_category_col)[value_column]
            .apply(lambda x: _safe_float(pd.to_numeric(x, errors="coerce").sum()))
            .nlargest(int(top_n))
        )
        result["top_items"] = [
            {"name": str(k), "value": _safe_float(v), "rank": i + 1}
            for i, (k, v) in enumerate(grouped.items())
        ]
        total = result["statistics"]["total"] or 1
        for item in result["top_items"]:
            item["share_pct"] = round(item["value"] / total * 100, 1)
        result["category_column"] = active_category_col
    else:
        result["category_column"] = category_column

    # Monthly trends
    if date_column and date_column in df.columns:
        try:
            df["_date"] = pd.to_datetime(df[date_column], errors="coerce")
            df["_month"] = df["_date"].dt.to_period("M").astype(str)
            monthly = (
                df.groupby("_month")[value_column]
                .apply(lambda x: _safe_float(pd.to_numeric(x, errors="coerce").sum()))
                .reset_index()
            )
            monthly.columns = ["month", "value"]
            monthly = monthly.sort_values("month")
            result["monthly_trends"] = [
                {"month": str(r["month"]), "value": _safe_float(r["value"])}
                for _, r in monthly.iterrows()
            ]
        except Exception as e:
            result["monthly_trends_error"] = str(e)

    # Pearson correlation analysis
    if correlation_column:
        if correlation_column not in df.columns:
            raise ValueError(
                f"Correlation column '{correlation_column}' not found. Available: {list(df.columns)}"
            )

        s_val = pd.to_numeric(df[value_column], errors="coerce")
        s_corr = pd.to_numeric(df[correlation_column], errors="coerce")
        valid_df = pd.DataFrame({"x": s_val, "y": s_corr}).dropna()

        n_valid = int(len(valid_df))
        std_x = valid_df["x"].std() if n_valid > 1 else 0.0
        std_y = valid_df["y"].std() if n_valid > 1 else 0.0

        if n_valid < 2 or std_x == 0.0 or std_y == 0.0 or pd.isna(std_x) or pd.isna(std_y):
            result["correlation"] = {
                "column_1": value_column,
                "column_2": correlation_column,
                "coefficient": None,
                "sample_size": n_valid,
                "interpretation": (
                    "Correlation cannot be computed because one or both columns have zero variance "
                    "(constant values) or fewer than 2 valid data points."
                ),
            }
        else:
            raw_r = float(valid_df["x"].corr(valid_df["y"], method="pearson"))
            if math.isnan(raw_r) or math.isinf(raw_r):
                result["correlation"] = {
                    "column_1": value_column,
                    "column_2": correlation_column,
                    "coefficient": None,
                    "sample_size": n_valid,
                    "interpretation": "Correlation could not be computed (indeterminate result).",
                }
            else:
                r_val = round(raw_r, 4)
                if r_val >= 0.7:
                    interp = "Strong positive linear correlation"
                elif 0.3 <= r_val < 0.7:
                    interp = "Moderate positive linear correlation"
                elif -0.3 < r_val < 0.3:
                    interp = "Weak or negligible linear correlation"
                elif -0.7 < r_val <= -0.3:
                    interp = "Moderate negative linear correlation"
                else:
                    interp = "Strong negative linear correlation"

                result["correlation"] = {
                    "column_1": value_column,
                    "column_2": correlation_column,
                    "coefficient": r_val,
                    "sample_size": n_valid,
                    "interpretation": interp,
                }
        result["correlation_column"] = correlation_column

    result["value_column"] = value_column
    result["category_column"] = category_column
    result["date_column"] = date_column

    return result
