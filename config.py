"""Application configuration loaded from environment variables.

For local development, set ENV=local to auto-load a .env file via python-dotenv.
In production (Azure Functions), environment variables are set via Application Settings.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

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

    On a TLS-inspecting corporate VPN, certifi alone is not enough — the
    inspected hosts present certs signed by the corp CA. So when a corp CA
    bundle is configured (``AZURE_OPENAI_CA_BUNDLE``, e.g. certifi + corp
    roots), prefer it as the replacement so every requests-based client
    (ROR, SerpAPI, page fetch) survives the VPN too. Fall back to certifi
    when no corp bundle is available.
    """
    certifi_path = certifi.where()
    corp_bundle = os.environ.get("AZURE_OPENAI_CA_BUNDLE")
    replacement = (
        corp_bundle if (corp_bundle and os.path.isfile(corp_bundle)) else certifi_path
    )
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        current = os.environ.get(var)
        if current and not os.path.isfile(current):
            os.environ[var] = replacement
            logger.warning(
                "%s pointed to non-existent path %r; overriding to %s",
                var, current, replacement,
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
    "LEI_LOOKUP_ENABLED": "true",
    "GLEIF_API_BASE": "https://api.gleif.org/api/v1",
    "GLEIF_TIMEOUT_SECONDS": "15",
    "LEI_NAME_MATCH_THRESHOLD": "88",
    "LEI_MAX_RETRIES": "2",
    "DOMAIN_NAME_MATCH_THRESHOLD": "82",
    "DOMAIN_OWNERSHIP_GUARD_ENABLED": "true",
    "FUZZY_MATCH_THRESHOLD": "80",
    "MAX_PAGE_CONTENT_CHARS": "3000",
    "DEFAULT_MAX_CONCURRENCY": "5",
    # Golden-record election: a duplicate merge whose adjudication confidence is
    # below this keeps its cluster membership but enters election as
    # manual_review (a human confirms before anything is blocked). Retuning it
    # never re-runs the LLM — election reads the confidence persisted by
    # clustering.
    "CONFIDENCE_MERGE_THRESHOLD": "0.95",
    # Dedup candidate NOMINATION (residue pass). A pair of signatures becomes an
    # LLM adjudication candidate when suffix-stripped name similarity (Jaro-
    # Winkler) reaches NAME_CANDIDATE_THRESHOLD, or token-set Jaccard reaches
    # TOKEN_CANDIDATE_THRESHOLD, or their ROR/LEI converge. Nomination never
    # merges — the LLM verdict decides. MAX_CANDIDATES_PER_BLOCK caps LLM calls
    # per block; over the cap the block routes to manual_review.
    "NAME_CANDIDATE_THRESHOLD": "0.85",
    "TOKEN_CANDIDATE_THRESHOLD": "0.6",
    "MAX_CANDIDATES_PER_BLOCK": "50",
    "PAGE_FETCH_TIMEOUT_SECONDS": "10",
    # Fix 3 — page-read corroborator.
    "PAGE_CORROBORATION_ENABLED": "true",
    "PAGE_NAME_MATCH_THRESHOLD": "88",
    "PAGE_READ_TIMEOUT_SECONDS": "8",
    "PAGE_FIXTURE_DIR": "tests/fixtures/page_reads",
    "PAGE_FIXTURE_REPLAY_ONLY": "false",
    "PAGE_EXTRACT_FEEDS_RETRY": "false",
    "MOCK_EXTERNAL_CALLS": "false",
    "ENV": "production",
    "LOG_LEVEL": "INFO",
    "DEPT_PROBE_CROSS_DOMAIN": "false",
    # Diagnostic-only: when true, the Path B / Path C website resolver emits a
    # structured per-candidate JSON trace on the `enrichment.trace.website`
    # logger. Purely additive — resolution behaviour is unchanged. Default off.
    "WEBSITE_TRACE": "false",
    # Diagnostic-only: when true, the Tier 1 re-lookup after canonicalisation
    # emits one structured JSON line per finalised record on the
    # `enrichment.trace.retry` logger — whether the retry was reached, why it
    # was skipped, which registries it queried, which guards rejected what.
    # Purely additive — retry behaviour is unchanged. Default off.
    "RETRY_TRACE": "false",
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
        default_factory=lambda: _bool(os.getenv("DEPT_PROBE_CROSS_DOMAIN"), default=False)
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

    # GLEIF / LEI (Tier 1 company registry — the company counterpart to ROR)
    # Feature flag so the lookup can be A/B tested or disabled cheaply; when
    # off the company branch behaves exactly as before (straight to the LLM).
    lei_lookup_enabled: bool = field(
        default_factory=lambda: _bool(os.getenv("LEI_LOOKUP_ENABLED"), default=True)
    )
    gleif_api_base: str = field(
        default_factory=lambda: os.getenv("GLEIF_API_BASE", "https://api.gleif.org/api/v1")
    )
    gleif_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("GLEIF_TIMEOUT_SECONDS", "15"))
    )
    # rapidfuzz token_sort_ratio (0-100). GLEIF's legalName filter is fulltext,
    # not exact, so a candidate below this is rejected to avoid fabricated
    # matches (e.g. "Personalvorsorgestiftung der Pfizer AG" for "Pfizer AG").
    lei_name_match_threshold: float = field(
        default_factory=lambda: float(os.getenv("LEI_NAME_MATCH_THRESHOLD", "88"))
    )
    lei_max_retries: int = field(
        default_factory=lambda: int(os.getenv("LEI_MAX_RETRIES", "2"))
    )

    # Domain ownership guard (utils/domain_resolver.py) — the domain-path
    # counterpart to ROR's country guard and GLEIF's name verification.
    # rapidfuzz token_sort_ratio (0-100) that Name 1 must reach against the
    # candidate's domain label before a web-derived domain is attributed to the
    # organisation. Tuned on the demo batch: the highest wrong-owner pair scores
    # 81.8 ("Acme Biotech" → aumbiotech.com) and the lowest right-owner pair
    # 82.4 ("Lockheed Martin Corp" → lockheedmartin.com), so 82 is the smallest
    # value that separates them. Registry provenance, email evidence and
    # on-domain search evidence each bypass this check.
    domain_name_match_threshold: float = field(
        default_factory=lambda: float(os.getenv("DOMAIN_NAME_MATCH_THRESHOLD", "82"))
    )
    # Feature flag so the guard can be A/B disabled. When off, candidates are
    # still canonicalised to the registrable domain — only the ownership
    # conditions are skipped.
    domain_ownership_guard_enabled: bool = field(
        default_factory=lambda: _bool(
            os.getenv("DOMAIN_OWNERSHIP_GUARD_ENABLED"), default=True
        )
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

    # ── Fix 3 — page-read corroborator (enrichment/page_corroborator.py) ──
    # Feature flag, following LEI_LOOKUP_ENABLED / DOMAIN_OWNERSHIP_GUARD_ENABLED.
    # When off the step does not run and no page is fetched.
    page_corroboration_enabled: bool = field(
        default_factory=lambda: _bool(
            os.getenv("PAGE_CORROBORATION_ENABLED"), default=True,
        )
    )
    # rapidfuzz token_sort_ratio (0-100) an extracted page name must reach
    # against Name 1 before the page counts as naming this organisation.
    #
    # DERIVATION: this is a supplied-name-vs-stated-legal-name comparison — the
    # same shape as GLEIF's name-verification guard, and it reuses that guard's
    # scorer verbatim (`enrichment.tier1_lei._name_match_score`: token_sort_ratio,
    # max of raw and legal-form-stripped). It therefore inherits that guard's
    # threshold and its derivation (LEI_NAME_MATCH_THRESHOLD = 88, tuned so
    # "Personalvorsorgestiftung der Pfizer AG" does not verify as "Pfizer AG").
    # It is a SEPARATE knob rather than a reference so that retuning the
    # registry guard does not silently retune what counts as a corroborating
    # page, and vice versa; the default is deliberately identical.
    page_name_match_threshold: float = field(
        default_factory=lambda: float(os.getenv("PAGE_NAME_MATCH_THRESHOLD", "88"))
    )
    # Hard per-request timeout for a corroboration fetch. Shorter than
    # PAGE_FETCH_TIMEOUT_SECONDS because this step is optional evidence on a
    # path that already has an answer: up to five requests may be issued per
    # domain (root plus the imprint probe), and a slow host must not dominate
    # the record's latency.
    page_read_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("PAGE_READ_TIMEOUT_SECONDS", "8"))
    )
    # Where page reads are recorded, one JSON file per domain. A page read is a
    # claim about what a site said on a given day, so it is kept on disk and
    # not only in memory: re-running a thesis batch must reproduce its
    # corroboration decisions rather than re-litigate them against today's web.
    # Set to "" to disable the fixture store (memory-only).
    page_fixture_dir: str = field(
        default_factory=lambda: os.getenv(
            "PAGE_FIXTURE_DIR", "tests/fixtures/page_reads",
        )
    )
    # Refuse to fetch anything not already recorded. A missing fixture then
    # surfaces as `fetch_unavailable` instead of a silent new network call —
    # what an offline re-analysis or a CI run wants.
    page_fixture_replay_only: bool = field(
        default_factory=lambda: _bool(
            os.getenv("PAGE_FIXTURE_REPLAY_ONLY"), default=False,
        )
    )
    # Optional: offer a page-extracted legal name to the Stage 5 Tier 1 retry
    # as a lookup candidate. OFF by default and deliberately so — Fix 1's trace
    # shows Stage 5's yield is bounded by GLEIF's coverage of private US SMBs,
    # not by the supply of candidate names, so this buys API calls before it
    # buys identifiers. Every retry guard still applies when it is on.
    page_extract_feeds_retry: bool = field(
        default_factory=lambda: _bool(
            os.getenv("PAGE_EXTRACT_FEEDS_RETRY"), default=False,
        )
    )

    # Concurrency
    default_max_concurrency: int = field(
        default_factory=lambda: int(os.getenv("DEFAULT_MAX_CONCURRENCY", "5"))
    )

    # Golden-record election (Phase 2 Pass 3). A merge below this confidence is
    # demoted to manual_review at election time; the threshold is a pure data
    # retune (no LLM re-run).
    confidence_merge_threshold: float = field(
        default_factory=lambda: float(os.getenv("CONFIDENCE_MERGE_THRESHOLD", "0.95"))
    )

    # Dedup candidate nomination (residue pass). Nominate-only thresholds — the
    # LLM verdict still decides; nomination never merges.
    name_candidate_threshold: float = field(
        default_factory=lambda: float(os.getenv("NAME_CANDIDATE_THRESHOLD", "0.85"))
    )
    token_candidate_threshold: float = field(
        default_factory=lambda: float(os.getenv("TOKEN_CANDIDATE_THRESHOLD", "0.6"))
    )
    max_candidates_per_block: int = field(
        default_factory=lambda: int(os.getenv("MAX_CANDIDATES_PER_BLOCK", "50"))
    )

    # Feature flags
    mock_external_calls: bool = field(
        default_factory=lambda: _bool(os.getenv("MOCK_EXTERNAL_CALLS"), default=False)
    )
    env: str = field(default_factory=lambda: os.getenv("ENV", "production"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    # Diagnostic-only per-candidate website-resolution trace (Path B / Path C).
    # Off by default; enabling it only adds logging, never changes resolution.
    website_trace: bool = field(
        default_factory=lambda: _bool(os.getenv("WEBSITE_TRACE"), default=False)
    )
    # Diagnostic-only per-record trace of the Tier 1 re-lookup after
    # canonicalisation (Stage 5). Off by default; enabling it only adds a JSON
    # line per finalised record on `enrichment.trace.retry` and never changes
    # whether a retry fires, which registry it queries, or what it writes.
    retry_trace: bool = field(
        default_factory=lambda: _bool(os.getenv("RETRY_TRACE"), default=False)
    )
    # Log file path. None => configure_logging uses its default
    # (logs/enrichment_api.log); set LOG_FILE="" to disable file logging.
    log_file: Optional[str] = field(default_factory=lambda: os.getenv("LOG_FILE"))


def get_settings() -> Settings:
    """Create a fresh Settings instance from current environment."""
    return Settings()
