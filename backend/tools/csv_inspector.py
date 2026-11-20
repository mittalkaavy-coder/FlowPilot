import pandas as pd
from core.security import resolve_upload_path


def run(file_id: str) -> dict:
    """Inspect a CSV file: schema, dtypes, row count, null counts, sample rows."""
    path = resolve_upload_path(file_id)
    df = pd.read_csv(path)

    columns = []
    for col in df.columns:
        sample = df[col].dropna().head(3).tolist()
        # Convert numpy types to native Python
        sample = [v.item() if hasattr(v, "item") else v for v in sample]
        columns.append({
            "name": col,
            "dtype": str(df[col].dtype),
            "null_count": int(df[col].isnull().sum()),
            "unique_count": int(df[col].nunique()),
            "sample_values": sample,
        })

    # Detect likely column roles
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    date_cols = [
        c for c in df.columns
        if any(k in c.lower() for k in ["date", "time", "month", "year", "day"])
    ]
    category_cols = df.select_dtypes(include=["object", "category", "string"]).columns.tolist()

    sample_rows = []
    for row in df.head(5).to_dict(orient="records"):
        clean = {k: (v.item() if hasattr(v, "item") else v) for k, v in row.items()}
        sample_rows.append(clean)

    return {
        "file_id": file_id,
        "shape": {"rows": int(len(df)), "columns": int(len(df.columns))},
        "columns": columns,
        "numeric_columns": numeric_cols,
        "date_columns": date_cols,
        "category_columns": category_cols,
        "sample_rows": sample_rows,
    }
