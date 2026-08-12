"""FastAPI middleware for structured JSON logging, request timing, and error handling."""

from __future__ import annotations

import logging
import os
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration in structured JSON format."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())[:8]
        start = time.perf_counter()

        # Attach request_id for downstream correlation
        request.state.request_id = request_id

        logger.info(
            "request_start",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": str(request.url.path),
            },
        )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.exception(
                "request_error",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": str(request.url.path),
                    "duration_ms": duration_ms,
                },
            )
            # Return 500 only for truly unhandled middleware-level errors
            return Response(
                content='{"detail":"Internal server error"}',
                status_code=500,
                media_type="application/json",
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Duration-MS"] = str(duration_ms)

        logger.info(
            "request_complete",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": str(request.url.path),
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        return response


def configure_logging(log_level: str = "INFO", log_file: str | None = None) -> None:
    """Set up logging to the console AND a rotating log file.

    The console handler keeps the live output you see while a run executes; the
    file handler captures the same records to ``log_file`` so a finished run can
    be debugged afterwards. The file path comes from the ``log_file`` argument,
    else the ``LOG_FILE`` env var, else ``logs/enrichment_api.log`` under the
    project root. Set ``LOG_FILE=""`` (empty) to disable file logging. The file
    rotates at ~10 MB, keeping 5 backups, so it never grows unbounded.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    fmt = (
        "%(asctime)s %(levelname)s %(name)s "
        "[%(funcName)s] %(message)s"
    )
    formatter = logging.Formatter(fmt)

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    # Resolve the log file: explicit arg > LOG_FILE env > default under logs/.
    # An explicitly empty LOG_FILE ("") disables file logging.
    if log_file is None:
        log_file = os.getenv("LOG_FILE")
    if log_file is None:
        log_file = str(Path(__file__).resolve().parent.parent / "logs" / "enrichment_api.log")

    if log_file:
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            handlers.append(file_handler)
        except OSError as exc:  # e.g. unwritable path — keep console logging alive
            logging.getLogger(__name__).warning(
                "Could not open log file %r (%s); logging to console only.",
                log_file, exc,
            )

    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(level=level, handlers=handlers, force=True)

    # Route uvicorn's own loggers through the root handlers (console + file) so
    # the "INFO: ... POST /enrich/file ... 200 OK" access lines and any server
    # errors are captured in the log file too — not just the app's own logs.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers = []
        uv.propagate = True

    if log_file and len(handlers) > 1:
        logging.getLogger(__name__).info("Logging to file: %s", log_file)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
