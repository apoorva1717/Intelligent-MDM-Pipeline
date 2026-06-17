"""Phase 2 "Pass 2" deduplication adjudicator.

Takes address-gated candidate records (same country + postal code + street,
already cleared by DATAshaper's address gates) and decides which of them are
true duplicates, producing clusters. See ``dedup.adjudicator`` for the
per-block algorithm and ``dedup.prompts`` for the LLM contract.
"""
