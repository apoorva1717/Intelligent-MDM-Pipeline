"""VAT ID validation: format check (python-stdnum) and VIES lookup.

Standalone. Nothing in ``enrichment/``, ``dedup/`` or the issue detector
imports from here, and a VAT result never feeds a name, a cluster or a score.
The only things borrowed are the evidence cache (``utils.cache``), the TLS
resolution the registry clients use, and the upload reader's header matching
(``api.routes._norm_header`` + the ``EnrichmentRecord`` column aliases).
See ``docs/VAT_VALIDATION.md``.
"""
