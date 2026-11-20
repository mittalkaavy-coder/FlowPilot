import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from core.config import settings
from core.cleanup import cleanup_old_files
from api.routes import files, plan, execute

logger = logging.getLogger(__name__)
_CLEANUP_INTERVAL_SECONDS = 60 * 60


async def _cleanup_loop(stop_event: asyncio.Event) -> None:
    """Run periodic cleanup off the event loop until application shutdown."""
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(cleanup_old_files)
        except Exception as exc:
            logger.error("Automatic cleanup failed (%s)", type(exc).__name__)

        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=_CLEANUP_INTERVAL_SECONDS
            )
        except asyncio.TimeoutError:
            continue


@asynccontextmanager
async def lifespan(application: FastAPI):
    stop_event = asyncio.Event()
    cleanup_task = None
    if settings.cleanup_enabled:
        cleanup_task = asyncio.create_task(
            _cleanup_loop(stop_event), name="flowpilot-file-cleanup"
        )
    application.state.cleanup_task = cleanup_task

    try:
        yield
    finally:
        if cleanup_task is not None:
            stop_event.set()
            await cleanup_task


app = FastAPI(
    title="FlowPilot API",
    description="Agentic Workflow Automation Backend",
    version="1.0.0",
    lifespan=lifespan,
)


def configure_cors(application: FastAPI, app_settings=settings) -> None:
    """Apply the configured CORS policy, rejecting unsafe wildcard credentials."""
    if "*" in app_settings.allowed_origins and app_settings.cors_allow_credentials:
        raise ValueError(
            "Wildcard CORS origins cannot be used when credentialed requests are enabled"
        )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=app_settings.cors_allow_credentials,
        # Preserve compatibility with the existing API methods and Axios headers.
        allow_methods=["*"],
        allow_headers=["*"],
    )


configure_cors(app)

app.include_router(files.router)
app.include_router(plan.router)
app.include_router(execute.router)


@app.get("/health")
def health_check():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/health/diagnostic")
def credential_diagnostic():
    """Return limited operational status only when explicitly enabled."""
    if not settings.diagnostics_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    storage_dirs = (settings.upload_dir, settings.report_dir)
    return {
        "status": "ok",
        "cleanup_enabled": settings.cleanup_enabled,
        "storage_writable": all(
            directory.is_dir() and os.access(directory, os.W_OK)
            for directory in storage_dirs
        ),
    }
