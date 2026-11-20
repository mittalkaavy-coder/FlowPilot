import os
from pathlib import Path
from dotenv import load_dotenv

# Explicitly locate .env relative to this file's location:
# 1. backend/.env (FlowPilot/backend/.env)
# 2. root .env (FlowPilot/.env)
BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


def _absolute_path(value: str | Path, base: Path) -> Path:
    """Resolve a configured path independently of the process working directory."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()

for candidate in [BACKEND_DIR / ".env", ROOT_DIR / ".env"]:
    if candidate.is_file():
        load_dotenv(dotenv_path=candidate, override=True)
        break


def _parse_bool_setting(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


class Settings:
    def __init__(self):
        raw_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        self.gemini_api_key: str = raw_key.strip("'\" \t\r\n")
        self.GEMINI_API_KEY: str = self.gemini_api_key

        raw_model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        self.gemini_model: str = (raw_model or "gemini-3.8-flash").strip("'\" \t\r\n")
        self.GEMINI_MODEL: str = self.gemini_model

        raw_fallback = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite")
        self.gemini_fallback_model: str = (raw_fallback or "gemini-3.1-flash-lite").strip("'\" \t\r\n")
        self.GEMINI_FALLBACK_MODEL: str = self.gemini_fallback_model

        # Client-side request timeout for each Gemini call (in seconds)
        # Gemini API enforces a minimum deadline of 10 seconds (10000ms).
        raw_timeout = os.getenv("GEMINI_TIMEOUT_SECONDS", "10")
        try:
            parsed_timeout = int(raw_timeout)
        except ValueError:
            parsed_timeout = 10
        self.gemini_timeout_seconds: int = max(10, parsed_timeout)

        # Maximum overall wall-clock deadline for the entire planning operation (in seconds)
        # Set to 55s: guaranteed to exit and return HTTP 503 at least 35s before frontend 90s timeout.
        raw_deadline = os.getenv("PLANNER_TOTAL_DEADLINE_SECONDS", "55")
        try:
            self.planner_total_deadline_seconds: float = float(raw_deadline)
        except ValueError:
            self.planner_total_deadline_seconds = 55.0

        # Storage defaults to the backend directory, which contains the existing
        # default database. Relative storage settings are always anchored there
        # (or at FLOWPILOT_STORAGE_ROOT), never at the launcher's current directory.
        db_env = os.getenv("FLOWPILOT_DB_PATH", "flowpilot.db")
        storage_root_env = os.getenv("FLOWPILOT_STORAGE_ROOT")
        if storage_root_env:
            self.storage_root = _absolute_path(storage_root_env, BACKEND_DIR)
        elif Path(db_env).expanduser().is_absolute():
            # Keep an existing absolute FLOWPILOT_DB_PATH override as the root
            # for the other persistent artifacts when no root is set explicitly.
            self.storage_root = Path(db_env).expanduser().resolve().parent
        else:
            self.storage_root = BACKEND_DIR.resolve()

        self.db_path: Path = _absolute_path(db_env, self.storage_root)
        # Optional PostgreSQL foundation. The existing SQLite path remains the
        # active runtime until an explicit, reviewed cutover is performed.
        self.database_url: str | None = os.getenv("FLOWPILOT_DATABASE_URL") or None
        self.upload_dir: Path = (self.storage_root / "uploads").resolve()
        self.report_dir: Path = (self.storage_root / "reports").resolve()
        self.max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_MB", "10")) * 1024 * 1024
        self.diagnostics_enabled = _parse_bool_setting(
            "FLOWPILOT_DIAGNOSTICS_ENABLED", "false"
        )

        self.cleanup_enabled = _parse_bool_setting(
            "FLOWPILOT_CLEANUP_ENABLED", "false"
        )

        self.upload_retention_hours = self._retention_hours(
            "FLOWPILOT_UPLOAD_RETENTION_HOURS"
        )
        self.report_retention_hours = self._retention_hours(
            "FLOWPILOT_REPORT_RETENTION_HOURS"
        )

        default_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
        raw_origins = os.getenv("FLOWPILOT_ALLOWED_ORIGINS")
        configured_origins = (
            [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
            if raw_origins and raw_origins.strip()
            else []
        )
        self.allowed_origins: list[str] = list(dict.fromkeys(configured_origins)) or default_origins

        raw_credentials = os.getenv("FLOWPILOT_CORS_ALLOW_CREDENTIALS", "true").strip().lower()
        if raw_credentials in {"true", "1", "yes", "on"}:
            self.cors_allow_credentials = True
        elif raw_credentials in {"false", "0", "no", "off"}:
            self.cors_allow_credentials = False
        else:
            raise ValueError(
                "FLOWPILOT_CORS_ALLOW_CREDENTIALS must be a boolean value"
            )

        if "*" in self.allowed_origins and self.cors_allow_credentials:
            raise ValueError(
                "Wildcard CORS origins cannot be used when credentialed requests are enabled"
            )

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _retention_hours(variable_name: str) -> int:
        raw_value = os.getenv(variable_name, "24").strip()
        try:
            value = int(raw_value)
        except ValueError as exc:
            raise ValueError(f"{variable_name} must be a positive integer") from exc
        if value < 1:
            raise ValueError(f"{variable_name} must be at least 1 hour")
        return value


settings = Settings()
