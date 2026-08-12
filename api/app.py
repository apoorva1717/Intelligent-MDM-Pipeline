"""FastAPI application object — shared by both local and Azure Function entry points."""

from __future__ import annotations

from fastapi import FastAPI

from api.middleware import RequestLoggingMiddleware, configure_logging
from api.routes import router
from config import get_settings, validate_env

settings = get_settings()
configure_logging(settings.log_level, settings.log_file)

# FIX(Bug 6): warn at startup if required env vars are missing
validate_env()

app = FastAPI(
    title="SAP Customer Master Data Enrichment API",
    description=(
        "Intelligent enrichment of SAP customer name fields for Bruker "
        "Corporation MDM pipeline. Resolves incomplete, abbreviated, or "
        "incorrectly formatted institution and company names via ROR, "
        "contact lookup, department search, and LLM inference."
    ),
    version="1.0.0",
)

app.add_middleware(RequestLoggingMiddleware)
app.include_router(router)
