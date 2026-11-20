from tools.csv_inspector import run as csv_inspector_run
from tools.csv_analyzer import run as csv_analyzer_run
from tools.report_generator import run as report_generator_run
from tools.artifact_verifier import run as artifact_verifier_run

TOOL_REGISTRY: dict = {
    "csv_inspector": csv_inspector_run,
    "csv_analyzer": csv_analyzer_run,
    "report_generator": report_generator_run,
    "artifact_verifier": artifact_verifier_run,
}

TOOL_DESCRIPTIONS = {
    "csv_inspector": "Reads CSV schema, column names, data types, row count, null counts, and sample rows.",
    "csv_analyzer": "Performs statistical analysis: summary statistics, top-N items by category, monthly trends, and Pearson correlation between two numeric columns.",
    "report_generator": "Generates a Markdown report from analysis results and saves it to disk.",
    "artifact_verifier": "Verifies the generated report exists, checks file size, reads content.",
}

REQUIRED_ARGS = {
    "csv_inspector": {"file_id"},
    "csv_analyzer": {"file_id", "value_column"},
    "report_generator": {"file_id", "title"},
    "artifact_verifier": set(),
}
