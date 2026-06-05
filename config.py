"""Application configuration loaded from environment variables.

For local development, set ENV=local to auto-load a .env file via python-dotenv.
In production (Azure Functions), environment variables are set via Application Settings.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import certifi
from dotenv import load_dotenv

# Always attempt to load .env — the file may not exist in production
# (Azure Functions uses Application Settings instead), and load_dotenv()
# silently no-ops when the file is missing.  The old conditional
# `if os.getenv("ENV") == "local"` was a chicken-and-egg bug: ENV=local
# lived inside the .env file that hadn't been loaded yet.
load_dotenv()

logger = logging.getLogger(__name__)


def _sanitize_ssl_env() -> None:
    """Override bogus SSL_CERT_FILE / REQUESTS_CA_BUNDLE env vars at
    process startup.

    Windows corp environments often pre-set these to a placeholder
    path (e.g. ``C:\\path\\to\\corp-ca-bundle.pem``) that doesn't
    actually exist. Every library that uses ``requests`` (SerpAPI,
    page fetching, etc.) then fails with::

        OSError: Could not find a suitable TLS CA certificate bundle,
        invalid path: …

    The ROR client, OpenAI client, and PageFetcher already work
    around this by passing ``verify=certifi.where()`` explicitly,
    but third-party SDKs we don't control (e.g. ``serpapi``) can't
    be patched the same way. Overriding the env vars here makes the
    workaround global.
    """
    certifi_path = certifi.where()
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        current = os.environ.get(var)
        if current and not os.path.isfile(current):
            os.environ[var] = certifi_path
            logger.warning(
                "%s pointed to non-existent path %r; overriding to %s",
                var, current, certifi_path,
            )


_sanitize_ssl_env()


def _bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")


# ── Environment variable validation ──────────────────────────────────────────

REQUIRED_VARS = [
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
]

OPTIONAL_VARS_WITH_DEFAULTS = {
    "AZURE_OPENAI_DEPLOYMENT": "gpt-5.4",
    "ROR_API_BASE": "https://api.ror.org/v2/organizations",
    "ROR_CONFIDENCE_THRESHOLD": "0.8",
    "FUZZY_MATCH_THRESHOLD": "80",
    "MAX_PAGE_CONTENT_CHARS": "3000",
    "DEFAULT_MAX_CONCURRENCY": "5",
    "PAGE_FETCH_TIMEOUT_SECONDS": "10",
    "MOCK_EXTERNAL_CALLS": "false",
    "ENV": "production",
    "LOG_LEVEL": "INFO",
    "DEPT_PROBE_CROSS_DOMAIN": "true",
}


def validate_env() -> None:
    """Warn about missing required environment variables at startup.

    Does not raise — allows the app to start so health checks still work,
    but LLM calls will fail until the variables are set.
    """
    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        for var in missing:
            logger.warning("Missing required env var: %s", var)
        logger.warning(
            "LLM calls will fail until these are set. "
            "Copy .env.example to .env and fill in values."
        )

    serpapi_key = os.getenv("SERPAPI_KEY", "").strip()
    if serpapi_key:
        logger.info("Search provider: SerpAPI (key configured)")
    else:
        logger.warning(
            "SERPAPI_KEY is not set — falling back to DuckDuckGo. "
            "DuckDuckGo returns lower-quality results. "
            "Set SERPAPI_KEY in .env for better department search."
        )


# ── Settings dataclass ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of all configuration values."""

    # Azure OpenAI
    openai_api_key: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_KEY", ""))
    azure_openai_endpoint: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4"))

    # Search
    serpapi_key: str = field(default_factory=lambda: os.getenv("SERPAPI_KEY", ""))
    # When False (default) the department-domain probe issues at most one
    # SERP call (the site-restricted query). The cross-domain fallback
    # query — which catches departments hosted on a separate brand domain
    # (e.g. hopkinsmedicine.org) — only runs when this is enabled, so the
    # common case stays at one SERP call per record.
    dept_probe_cross_domain: bool = field(
        default_factory=lambda: _bool(os.getenv("DEPT_PROBE_CROSS_DOMAIN"), default=True)
    )

    # ROR
    ror_api_base: str = field(
        default_factory=lambda: os.getenv("ROR_API_BASE", "https://api.ror.org/v2/organizations")
    )
    # FIX(Bug 1): single confidence threshold for all record types.
    # Was: separate 0.8 for institutions, 0.9 for companies.
    ror_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("ROR_CONFIDENCE_THRESHOLD", "0.8"))
    )

    # Fuzzy matching
    fuzzy_match_threshold: int = field(
        default_factory=lambda: int(os.getenv("FUZZY_MATCH_THRESHOLD", "80"))
    )

    # Page fetching
    max_page_content_chars: int = field(
        default_factory=lambda: int(os.getenv("MAX_PAGE_CONTENT_CHARS", "1500"))
    )
    page_fetch_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("PAGE_FETCH_TIMEOUT_SECONDS", "10"))
    )

    # Concurrency
    default_max_concurrency: int = field(
        default_factory=lambda: int(os.getenv("DEFAULT_MAX_CONCURRENCY", "5"))
    )

    # Feature flags
    mock_external_calls: bool = field(
        default_factory=lambda: _bool(os.getenv("MOCK_EXTERNAL_CALLS"), default=False)
    )
    env: str = field(default_factory=lambda: os.getenv("ENV", "production"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


def get_settings() -> Settings:
    """Create a fresh Settings instance from current environment."""
    return Settings()
