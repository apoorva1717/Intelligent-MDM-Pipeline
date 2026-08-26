"""Tier 1 (company): GLEIF / LEI registry lookup.

The company counterpart to the ROR institution lookup (``tier1_ror``).
For company-type records this resolves the official legal name and a
Legal Entity Identifier from the free GLEIF API (no auth, JSON:API
format) and uses it as the canonical ``name1`` BEFORE the LLM company
canonicalization fallback.

Strategy (mirrors the ROR client's primary-then-fallback shape):

1. **Exact-ish (precise):** ``lei-records`` filtered by
   ``filter[entity.legalName]``, ``filter[entity.status]=ACTIVE`` and a
   country filter (``filter[entity.legalAddress.country]`` = the record's
   ISO alpha-2). NOTE: GLEIF's legalName filter is *fulltext*, not exact
   equality — "Pfizer" returns "PFIZER AG", "PFIZER INC.", etc. — so the
   verification guard below is mandatory even on this "precise" path.
2. **Fuzzy (recall):** ``fuzzycompletions?field=entity.legalName`` then
   resolve the candidate to its full ``lei-record``. Best-effort: GLEIF's
   typeahead frequently returns nothing, so a no-op here is normal and
   simply means a miss.

VERIFICATION GUARD (required): every candidate's ``legalName`` is scored
against the input name with rapidfuzz ``token_sort_ratio`` (both
lowercased — GLEIF returns names UPPERCASE). Candidates below
``LEI_NAME_MATCH_THRESHOLD`` are rejected. GLEIF fuzzy is statistical;
without this guard it fabricates matches.

COUNTRY GUARD (required): when a country is known, candidates whose
``legalAddress.country`` differs from it are rejected during selection on
BOTH paths. GLEIF's country filter only constrains the precise request, and
``fuzzycompletions`` cannot be country-filtered at the API — so without this
post-filter the fuzzy path returns same-name companies from the wrong
country (a different legal entity). See ``_best_verified_candidate``.

A GLEIF failure (timeout / 5xx / malformed) must NEVER fail the record —
every error path returns a miss/error dict and the orchestrator falls
through to the existing LLM path unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
from rapidfuzz import fuzz

from enrichment.locality import (
    CONSISTENT,
    compare_registry_addresses,
    strip_subdivision_prefix,
)
from enrichment.registry_match import (
    CROSSWALK_TIER,
    ambiguity_verdict,
    is_collision_prone,
    name_match_tier,
    rank_key,
    second_signal,
)
from llm.openai_client import resolve_tls_verify
from utils.cache import (
    CacheKey,
    RegistryUnavailableFrozen,
    cached_registry_get,
    legacy_lookup_key,
    lookup_key,
)

logger = logging.getLogger(__name__)

# JSON:API content type GLEIF speaks.
_GLEIF_ACCEPT = "application/vnd.api+json"

# Legal-form / entity-suffix tokens stripped before name verification, so an
# input that omits the legal form ("Novartis") still verifies against the
# official name that carries it ("NOVARTIS AG"). Deliberately ONLY true legal
# forms — never descriptive words like "products" / "holdings" / "group",
# which distinguish a subsidiary from its parent and must NOT be collapsed.
_LEGAL_FORM_TOKENS = {
    "ag", "inc", "incorporated", "llc", "ltd", "limited", "corp",
    "corporation", "co", "company", "gmbh", "sa", "sas", "sarl", "nv",
    "bv", "plc", "spa", "srl", "ab", "oyj", "oy", "as", "kg", "kgaa",
    "se", "pty", "llp", "lp", "pllc", "pc", "aps", "kk", "ulc",
}

_TOKEN_SPLIT_RE = re.compile(r"[a-z0-9]+")

#: How many ``fuzzycompletions`` candidates are resolved to their full
#: lei-record. A CALL budget, not a quality knob: each one costs a further
#: GLEIF request. The five taken are the five with the smallest LEI, not the
#: first five returned — see :func:`_fuzzy_lookup`.
_FUZZY_RESOLVE_LIMIT = 5


def _normalise_legal_name(s: str) -> str:
    """Lowercase, tokenise, and drop legal-form suffix tokens."""
    return " ".join(
        t for t in _TOKEN_SPLIT_RE.findall((s or "").lower())
        if t not in _LEGAL_FORM_TOKENS
    )

# Module-level cache — mirrors the ROR client's per-process cache and, like it,
# is keyed by ``utils.cache.lookup_key(name, country_code)``: the name
# normalised (lowercase, trim, collapse whitespace, strip punctuation, fold
# accents) plus the country filter. This namespace already carried country;
# what it gained is the punctuation/accent collapse, so "Lockheed Martin Corp."
# and "Lockheed Martin Corp" cost one GLEIF call, not two.
#
# Cache key only: `name` reaches GLEIF unnormalised, and _name_match_score()
# — the name-verification guard — never sees the key.
_lei_cache: "dict[CacheKey, dict[str, Any]]" = {}
_lei_legacy_seen: "set[CacheKey]" = set()
_lei_normalised_hits = 0


def clear_lei_cache() -> None:
    """Reset the module-level LEI cache (called per batch / between tests)."""
    global _lei_normalised_hits
    _lei_cache.clear()
    _lei_legacy_seen.clear()
    _lei_normalised_hits = 0


def lei_normalised_hits() -> int:
    """Lookups the normalised cache key saved that lowercasing would not."""
    return _lei_normalised_hits


def _name_match_score(query: str, legal_name: str) -> float:
    """Name-verification score (0-100) between the input and a candidate.

    The metric is rapidfuzz ``token_sort_ratio`` (per the design), taken as
    the max of the raw score and the score with legal-form suffixes stripped
    (AG / Inc / Ltd / GmbH …). The legal-form strip lets an input that omits
    the legal form ("Novartis") verify against the official name that carries
    it ("NOVARTIS AG") — WITHOUT the subset-collapse a ``token_set_ratio``
    would introduce: token_set scores ANY contained substring 100, which
    would wrongly accept "Personalvorsorgestiftung der Pfizer AG in
    Liquidation" (a different entity) for "Pfizer AG". token_sort stays
    length-sensitive, so that wrong entity scores ~21 and is rejected.

    GLEIF returns legal names UPPERCASE; both sides are lowercased.
    """
    q = (query or "").strip().lower()
    n = (legal_name or "").strip().lower()
    if not q or not n:
        return 0.0
    raw = fuzz.token_sort_ratio(q, n)
    nq, nn = _normalise_legal_name(query), _normalise_legal_name(legal_name)
    stripped = fuzz.token_sort_ratio(nq, nn) if (nq and nn) else 0.0
    return float(max(raw, stripped))


def _address(address: dict[str, Any] | None, kind: str) -> dict[str, Any] | None:
    """One GLEIF address block, normalised for the locality comparator.

    GLEIF's ``region`` is ISO 3166-2 ("US-NJ"), so the subdivision half is what
    the comparator wants. Returns None for an address that states no place at
    all — an empty block corroborates and contradicts nothing, and carrying it
    would only make the aggregate look like it had more evidence than it has.
    """
    address = address or {}
    # "US-TX" is the ISO 3166-2 code for the region the record calls "TX".
    # The rule is shared with the comparator rather than spelled out here, so
    # a lane that never reaches this parser still compares the two as equal.
    region = strip_subdivision_prefix(address.get("region"))
    out = {
        "kind": kind,
        "country": (address.get("country") or "").strip() or None,
        "city": (address.get("city") or "").strip() or None,
        "region": region or None,
        "postal_code": (address.get("postalCode") or "").strip() or None,
    }
    return out if any(v for k, v in out.items() if k != "kind") else None


def _record_fields(record: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields we care about from a GLEIF lei-record dict."""
    attrs = record.get("attributes", {}) or {}
    entity = attrs.get("entity", {}) or {}
    legal_name = (entity.get("legalName", {}) or {}).get("name")
    legal_address = entity.get("legalAddress", {}) or {}
    legal_form = entity.get("legalForm", {}) or {}
    # BOTH registered addresses. GLEIF publishes `legalAddress` (where the
    # entity is incorporated) and `headquartersAddress` (where it operates
    # from), and for a US company those are routinely different states —
    # Delaware and everywhere else. Comparing the record against the legal
    # address alone reported eleven false contradictions on the chemspeed
    # batch; the comparator takes the set and agrees with any of them. See
    # `enrichment.locality.compare_registry_addresses`.
    #
    # An entity whose two blocks are identical (the common case outside the
    # Delaware pattern) contributes one address, not two: a duplicate would
    # double-count one statement in the trace without changing the verdict.
    addresses: list[dict[str, Any]] = []
    for block, kind in (
        (legal_address, "legal"),
        (entity.get("headquartersAddress"), "headquarters"),
    ):
        entry = _address(block, kind)
        if entry is None:
            continue
        if any(
            {k: v for k, v in entry.items() if k != "kind"}
            == {k: v for k, v in seen.items() if k != "kind"}
            for seen in addresses
        ):
            continue
        addresses.append(entry)

    # The flat legal-address keys are unchanged and still the LEGAL address:
    # `country` is the country guard's input and must keep meaning the country
    # GLEIF filters on. The set above is what the locality comparison reads.
    region = strip_subdivision_prefix(legal_address.get("region"))
    # Every name GLEIF publishes for the entity, for the tier classifier —
    # parity with ROR, which has always ranked the record against every name
    # variant in `names[]` rather than the display name alone. A record that
    # states a registered trading name verbatim has named this entity exactly
    # as surely as one that states the legal name, and classifying it "fuzzy"
    # made the location advisory fire on a match the name had settled.
    entity_names: list[str] = [legal_name] if legal_name else []
    for block in ("otherNames", "transliteratedOtherNames"):
        for entry in entity.get(block) or []:
            value = ((entry or {}).get("name") or "").strip()
            if value and value not in entity_names:
                entity_names.append(value)

    return {
        "lei_id": record.get("id"),
        "legal_name": legal_name,
        "entity_names": entity_names,
        "status": entity.get("status"),
        # The liveness pair. `status` above is GLEIF's statement about the
        # ORGANISATION and is already used to rank candidates; this is its
        # statement about the REGISTRATION, and the two disagree in a way that
        # matters — Celgene Corporation is entity.status=ACTIVE and
        # registration.status=LAPSED. Carried, not judged: which values mean
        # the entity is gone (and, emphatically, which do not) is
        # `enrichment.liveness`'s decision, measured against the whole
        # register rather than read off the spec.
        "registration_status": (
            (attrs.get("registration") or {}).get("status")
        ),
        "country": legal_address.get("country"),
        "city": (legal_address.get("city") or "").strip() or None,
        "region": region or None,
        "postal_code": (legal_address.get("postalCode") or "").strip() or None,
        "addresses": addresses,
        # Classification evidence, carried through untouched for
        # enrichment.classifier. An LEI alone says nothing about commercial
        # status; these fields are the part of the response that does.
        # `category` is GENERAL for the overwhelming majority (MIT and Pfizer
        # both), so `legalForm.id` — an ISO 20275 ELF code — does most of the
        # work, with `legalForm.other` covering the 8888/9999 catch-alls.
        "category": entity.get("category"),
        "sub_category": entity.get("subCategory"),
        "legal_form_id": legal_form.get("id"),
        "legal_form_other": legal_form.get("other"),
    }


def _region_agrees(
    fields: dict[str, Any], city: str | None, state: str | None,
) -> bool:
    """True when the record's locality agrees with ANY registered address.

    The same set and the same comparator the locality verdict is built from —
    ``legalAddress`` and ``headquartersAddress`` both — so selection and the
    advisory can never disagree about where the registry says an entity is.
    ``False`` when the record states no region: silence is not agreement, and
    a record without a region ranks its candidates exactly as before.
    """
    if not (state or "").strip():
        return False
    verdict, _, _, _ = compare_registry_addresses(
        fields.get("addresses") or [], city=city, region=state,
    )
    return verdict == CONSISTENT


def _best_verified_candidate(
    name: str,
    records: list[dict[str, Any]],
    threshold: float,
    country_code: str | None = None,
    rejections: list[dict[str, Any]] | None = None,
    *,
    city: str | None = None,
    state: str | None = None,
    record_domain: str | None = None,
) -> tuple[dict[str, Any] | None, float, str | None]:
    """Pick the best name-verified candidate from a list of lei-records.

    Returns ``(fields, best_score, refusal)``. *refusal* is ``None`` on a clean
    outcome, or names the rule that refused an otherwise-passing candidate —
    ``"ambiguous"`` or ``"short_name_uncorroborated"``.

    Order of business, and every step of it is a REFUSAL rule; nothing here
    accepts anything the previous version rejected:

    1. **Country guard** (unchanged). GLEIF's
       ``filter[entity.legalAddress.country]`` only constrains the *exact*
       path, and ``fuzzycompletions`` cannot be country-filtered at the API at
       all — so without this post-filter the fuzzy path returns a same-name
       company from the wrong country, which is a different legal entity.
    2. **Name verification** (unchanged, same threshold). GLEIF's own search
       returns confident wrong answers.
    3. **Fix C(1) — a total order.** Survivors are sorted by
       ``(ACTIVE first, score DESC, LEI ASC)``. This was ``rank > best_rank``
       over the response in the order GLEIF returned it, so two candidates with
       the same status and score were separated by nothing but that order —
       and one chemspeed record attached a different company's ROR/LEI on each
       of two runs.
    4. **Fix C(2) — a near-tie is a no-match.** If the top two verified
       candidates are within the ambiguity margin, neither is returned. An
       entity that can oscillate between two answers resolves to neither.
    5. **Fix D(2) — the registered locality is compared** to the record's
       city/state with the shared comparator. A contradiction does NOT reject
       the match on its own (same-country relocations are common); it is
       carried out on the result as ``location_verdict`` for the orchestrator
       to flag.
    6. **Fix C(3) — a collision-prone name needs a second signal.** For "BHS"
       or "BIC", a verified name match is not enough: the locality must agree
       or the candidate's domain must be the record's. Otherwise no match.
       Combined with step 5, a short name whose locality *contradicts* the
       record is refused outright.
    """
    want_country = (country_code or "").strip().upper() or None
    verified: list[tuple[tuple, dict[str, Any], float]] = []
    # Every country-passing candidate as ``(score, LEI, is_active)``, verified
    # or not. The ambiguity check reads this rather than the verified list: a
    # runner-up a hair BELOW the threshold is exactly as capable of overtaking
    # the winner on the next re-index as one a hair above it.
    near_misses: list[tuple[float, str, bool]] = []
    best_score = 0.0

    for rec in records:
        fields = _record_fields(rec)
        if not fields.get("lei_id") or not fields.get("legal_name"):
            continue
        if want_country:
            # BOTH addresses, for the same reason the locality comparison
            # reads both: an entity incorporated abroad and headquartered in
            # the record's country is in that country, and the legal address
            # alone says otherwise. Agreement with either one is agreement.
            cand_countries = {
                (a.get("country") or "").strip().upper()
                for a in (fields.get("addresses") or [])
            } or {(fields.get("country") or "").strip().upper()}
            cand_country = (fields.get("country") or "").strip().upper()
            if want_country not in cand_countries:
                logger.info(
                    "GLEIF: rejecting '%s' (LEI=%s) — country %s != requested %s",
                    fields.get("legal_name"), fields.get("lei_id"),
                    cand_country or "?", want_country,
                )
                if rejections is not None:
                    rejections.append({
                        "guard": "gleif_country",
                        "candidate_name": fields.get("legal_name"),
                        "candidate_id": fields.get("lei_id"),
                        "score": None,
                        "threshold": threshold,
                        "detail": (
                            "no registered address in the requested country "
                            f"({'/'.join(sorted(c for c in cand_countries if c)) or '?'} "
                            f"!= {want_country})"
                        ),
                        "query": name,
                    })
                continue
        score = _name_match_score(name, fields["legal_name"])
        best_score = max(best_score, score)
        near_misses.append((
            score, fields.get("lei_id") or "",
            fields.get("status") == "ACTIVE",
            _region_agrees(fields, city, state),
        ))
        if score < threshold:
            # The name-verification guard. GLEIF's own search returns confident
            # wrong answers (a substring company at ~21 on this scale), so a
            # candidate the registry offered and this rule refused is exactly
            # the decision worth being able to defend afterwards.
            if rejections is not None:
                rejections.append({
                    "guard": "gleif_name_verification",
                    "candidate_name": fields.get("legal_name"),
                    "candidate_id": fields.get("lei_id"),
                    "score": score,
                    "threshold": threshold,
                    "detail": "legal-name match score below the guard threshold",
                    "query": name,
                })
            continue
        is_active = 1 if (fields.get("status") == "ACTIVE") else 0
        # The record's region against BOTH registered addresses, as a
        # DISCRIMINATOR among candidates the name guard already passed.
        # Name verification is the gate; where two entities are both plausible
        # by name, the one the registry places where the record is, is the one
        # the record means. "Cargill, Incorporated" and "Cargill Foundation"
        # are both Cargill by name; only one of them is where the record says.
        # Ranked BELOW registration status and ABOVE score, because every
        # candidate here has already cleared the name threshold — and it is
        # inert when the record states no region, so a record without one
        # ranks exactly as it did before.
        region_agrees = 1 if _region_agrees(fields, city, state) else 0
        verified.append(
            (
                rank_key(score, fields["lei_id"], -is_active, -region_agrees),
                fields,
                score,
            ),
        )

    if not verified:
        return None, best_score, None

    verified.sort(key=lambda t: t[0])

    # Fix C(2) — the winner and the next-best candidate GLEIF offered. The
    # runner-up is taken from every country-passing candidate, not only the
    # verified ones: a candidate a hair below the guard threshold can overtake
    # the winner on the registry's next re-index just as easily as one a hair
    # above it, and that is the flip this rule exists to refuse.
    winner_id = verified[0][1].get("lei_id") or ""
    winner_score = verified[0][2]
    winner_active = verified[0][1].get("status") == "ACTIVE"
    winner_agrees = _region_agrees(verified[0][1], city, state)
    # Same registration status only. An inactive entity is not a plausible
    # alternative reading of an active one — the registry HAS distinguished
    # them — so it cannot make the active winner ambiguous.
    # A candidate the registry places somewhere the record is not cannot make
    # an otherwise-identified winner ambiguous: the region has already told
    # the two apart, which is what asking it during selection is FOR. When the
    # winner has no region agreement to stand on, nothing is excluded and the
    # rule is exactly as strict as it was.
    others = [
        (sc, lei) for sc, lei, active, agrees in near_misses
        if lei != winner_id and active == winner_active
        and not (winner_agrees and not agrees)
    ]
    if others and ambiguity_verdict([winner_score, max(others)[0]]):
        first = verified[0][1]
        runner_up_score, runner_up_id = max(others)
        logger.info(
            "GLEIF: refusing '%s' — '%s' (%s) and %s score within the "
            "ambiguity margin; the registry has not identified one entity",
            name[:60], first.get("legal_name"), winner_id, runner_up_id,
        )
        if rejections is not None:
            rejections.append({
                "guard": "registry_ambiguity",
                "candidate_name": first.get("legal_name"),
                "candidate_id": winner_id,
                "score": winner_score,
                "threshold": threshold,
                "detail": (
                    f"within the ambiguity margin of {runner_up_id} "
                    f"({runner_up_score:.1f} vs {winner_score:.1f})"
                ),
                "query": name,
            })
        return None, best_score, "ambiguous"

    fields = dict(verified[0][1])
    score = verified[0][2]

    # Fix D(2) — the registered locality, compared and carried, never acted on
    # here. Against BOTH addresses GLEIF publishes: agreement with either one
    # is agreement, and only a disagreement with every one of them is a
    # contradiction.
    verdict, detail, scope, notes = compare_registry_addresses(
        fields.get("addresses") or [],
        city=city, region=state, postal_code=None,
    )
    fields["location_verdict"] = verdict
    fields["location_detail"] = detail
    fields["location_scope"] = scope
    fields["location_notes"] = notes
    # How strongly the NAME identified this entity. The locality flag is an
    # advisory about a match that might be the wrong organisation, and a
    # verbatim name match is not that match — see `registry_match`.
    fields["name_match_tier"] = name_match_tier(
        [name], fields.get("entity_names") or [fields.get("legal_name")],
    )

    # Fix C(3).
    if is_collision_prone(name):
        signal = second_signal(
            location_verdict=verdict,
            candidate_domain=None,  # GLEIF publishes no website field
            record_domain=record_domain,
        )
        if signal is None:
            logger.info(
                "GLEIF: refusing '%s' → '%s' (LEI=%s) — the name is too short "
                "to identify an entity on its own and nothing corroborates it "
                "(location=%s)",
                name[:60], fields.get("legal_name"), fields.get("lei_id"),
                verdict,
            )
            if rejections is not None:
                rejections.append({
                    "guard": "short_name_uncorroborated",
                    "candidate_name": fields.get("legal_name"),
                    "candidate_id": fields.get("lei_id"),
                    "score": score,
                    "threshold": threshold,
                    "detail": (
                        "collision-prone name with no corroborating signal "
                        f"(location={verdict})"
                    ),
                    "query": name,
                })
            return None, best_score, "short_name_uncorroborated"
        fields["corroborated_by"] = signal

    return fields, best_score, None


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str] | None,
    max_retries: int,
) -> dict[str, Any]:
    """GET *url* and parse JSON, retrying transient errors with backoff.

    Raises on a final failure so the caller's except blocks can classify
    it as an error (vs. a clean miss).
    """
    async def _live() -> dict[str, Any]:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    attempt = 0
    while True:
        try:
            # Through the evidence cache: the RESPONSE is what is recorded,
            # never the verification decision the caller goes on to make, so a
            # change to the guards is re-applied on every run. Retries wrap the
            # cached call, so a recorded response costs no attempts at all.
            return await cached_registry_get("gleif", url, params, _live)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            # Only retry transient failures (network, timeout, 5xx). A 4xx
            # is not going to get better on retry.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            transient = status is None or status >= 500
            attempt += 1
            if not transient or attempt > max_retries:
                raise
            backoff = 0.5 * (2 ** (attempt - 1))
            logger.info(
                "GLEIF transient error (%s), retry %d/%d after %.1fs",
                status or type(exc).__name__, attempt, max_retries, backoff,
            )
            await asyncio.sleep(backoff)


async def call_lei(
    name: str,
    country_code: str | None = None,
    base_url: str = "https://api.gleif.org/api/v1",
    timeout: float = 15.0,
    max_retries: int = 2,
    threshold: float = 88.0,
    *,
    city: str | None = None,
    state: str | None = None,
    record_domain: str | None = None,
) -> dict[str, Any]:
    """Look up a company's official legal name + LEI from GLEIF.

    Returns a dict:
      * verified match — ``{"matched": True, "lei_id", "legal_name",
        "country", "status", "strategy": "exact"|"fuzzy",
        "confidence": "high"|"medium", "score"}``
      * miss / below-threshold — ``{"matched": False, "strategy", "score"}``
      * API error / timeout — ``{"matched": False, "error": True}``

    Never raises — a GLEIF failure must not fail the record.
    """
    if not name or not name.strip():
        return {"matched": False, "strategy": None, "score": 0.0}

    # Candidates GLEIF offered and a guard refused (Fix 10 Step 4). Returned
    # with the result and written to the record's provenance log by the
    # orchestrator. Recording only — no decision changes.
    guard_rejections: list[dict[str, Any]] = []

    # Cache key only — the GLEIF request below uses `name` verbatim.
    global _lei_normalised_hits
    cache_key = lookup_key(name, country_code)
    legacy_key = legacy_lookup_key(name, country_code)
    if cache_key in _lei_cache:
        if legacy_key not in _lei_legacy_seen:
            _lei_normalised_hits += 1
        return _lei_cache[cache_key]
    _lei_legacy_seen.add(legacy_key)

    base = base_url.rstrip("/")
    records_url = f"{base}/lei-records"

    def _cache(result: dict[str, Any]) -> dict[str, Any]:
        """Memory-cache the decision for the rest of this batch.

        Memory only, and it dies with the batch. The registry's raw responses
        are what outlive the process — see `utils.cache.cached_registry_get`
        for why a decision must not be the thing that is frozen.
        """
        _lei_cache[cache_key] = result
        return result

    try:
        # verify=resolve_tls_verify() — reuse the OpenAI client's TLS trust
        # resolution so GLEIF survives a corporate TLS-inspecting VPN. On
        # such a VPN the public certifi bundle fails the handshake
        # ("CERTIFICATE_VERIFY_FAILED: unable to get local issuer
        # certificate"); resolve_tls_verify() prefers a configured corp CA
        # bundle (AZURE_OPENAI_CA_BUNDLE / REQUESTS_CA_BUNDLE / SSL_CERT_FILE)
        # and falls back to certifi off-VPN.
        async with httpx.AsyncClient(
            timeout=timeout, verify=resolve_tls_verify(),
            headers={"Accept": _GLEIF_ACCEPT},
        ) as client:
            # ── Strategy A: precise filter (legalName + ACTIVE + country) ──
            params: dict[str, str] = {
                "filter[entity.legalName]": name,
                "filter[entity.status]": "ACTIVE",
                "page[size]": "10",
            }
            if country_code:
                params["filter[entity.legalAddress.country]"] = country_code

            logger.info(
                "GLEIF exact request: name='%s' country=%s",
                name[:80], country_code,
            )
            data = await _get_json(client, records_url, params, max_retries)
            records = data.get("data", []) or []

            fields, best_score, refusal = _best_verified_candidate(
                name, records, threshold, country_code=country_code,
                rejections=guard_rejections,
                city=city, state=state, record_domain=record_domain,
            )
            if fields is not None:
                result = {
                    "matched": True,
                    "strategy": "exact",
                    "confidence": "high",
                    "score": best_score,
                    "guard_rejections": guard_rejections,
                    **fields,
                }
                logger.info(
                    "GLEIF exact matched '%s' → '%s' (LEI=%s, score=%.1f)",
                    name[:60], fields["legal_name"], fields["lei_id"], best_score,
                )
                return _cache(result)

            logger.info(
                "GLEIF exact: no verified candidate for '%s' "
                "(%d records, best score=%.1f < %.1f), trying fuzzy",
                name[:60], len(records), best_score, threshold,
            )

            # ── Strategy B: fuzzycompletions fallback (best-effort) ────────
            fuzzy_result = await _fuzzy_lookup(
                client, base, records_url, name, country_code,
                max_retries, threshold, rejections=guard_rejections,
                city=city, state=state, record_domain=record_domain,
            )
            if fuzzy_result is not None:
                fuzzy_result["guard_rejections"] = guard_rejections
                return _cache(fuzzy_result)

            return _cache({
                "matched": False, "strategy": "fuzzy", "score": best_score,
                "guard_rejections": guard_rejections,
                "refused_by": refusal,
            })

    except RegistryUnavailableFrozen:
        # CACHE_FROZEN and nothing recorded for this request. A clean miss,
        # already traced; NOT cached, because "we were not allowed to look" is
        # not an answer about the name.
        logger.info("GLEIF: frozen cache has no response for '%s'", name[:80])
        return {"matched": False, "strategy": None, "score": 0.0,
                "guard_rejections": []}
    except httpx.HTTPStatusError as exc:
        logger.error(
            "GLEIF HTTP %d for '%s': %s",
            exc.response.status_code, name[:80], exc.response.text[:200],
        )
        return {"matched": False, "error": True}
    except Exception:
        logger.exception("GLEIF lookup failed for '%s'", name[:80])
        return {"matched": False, "error": True}


async def _fuzzy_lookup(
    client: httpx.AsyncClient,
    base: str,
    records_url: str,
    name: str,
    country_code: str | None,
    max_retries: int,
    threshold: float,
    rejections: list[dict[str, Any]] | None = None,
    *,
    city: str | None = None,
    state: str | None = None,
    record_domain: str | None = None,
) -> dict[str, Any] | None:
    """fuzzycompletions → resolve candidate → verify. Returns a match dict
    or ``None`` (no usable/verified candidate). Best-effort: GLEIF's
    typeahead often returns nothing, which is a normal miss."""
    fuzzy_url = f"{base}/fuzzycompletions"
    data = await _get_json(
        client, fuzzy_url,
        {"field": "entity.legalName", "q": name},
        max_retries,
    )
    completions = data.get("data", []) or []
    if not completions:
        return None

    # Resolve each completion to its full lei-record, verify, keep the best.
    #
    # Fix C(1). `completions[:5]` is a CALL budget — each completion resolved
    # costs one more GLEIF request — but taking the first five *as returned*
    # let the typeahead's arrival order decide which candidates were even
    # looked at, which is the same defect as ROR's old `items[:10]`. There the
    # cap could simply be removed, because scoring is local and free; here it
    # cannot, so the truncation is made deterministic instead: the LEI is the
    # candidate's canonical id, and the five smallest are always the same five
    # whatever order GLEIF lists them in.
    #
    # Measured: this was the last order dependence in the pipeline. With every
    # recorded candidate list reversed (`tools/shuffle_evidence.py`), exactly
    # one record of the chemspeed batch changed its answer — AkzoNobel, which
    # saw a different five completions and so a different LEI. The double-run
    # diff could not see it; only perturbing the order could.
    ordered: list[str] = []
    seen: set[str] = set()
    for comp in completions:
        rel = (
            ((comp.get("relationships") or {}).get("lei-records") or {})
            .get("data") or {}
        )
        lei = rel.get("id")
        if lei and lei not in seen:
            seen.add(lei)
            ordered.append(lei)

    candidate_records: list[dict[str, Any]] = []
    for lei in sorted(ordered)[:_FUZZY_RESOLVE_LIMIT]:
        try:
            rec_data = await _get_json(
                client, f"{records_url}/{lei}", None, max_retries,
            )
        except Exception:
            logger.info("GLEIF fuzzy: failed to resolve LEI %s", lei)
            continue
        rec = rec_data.get("data")
        if isinstance(rec, dict):
            candidate_records.append(rec)

    fields, best_score, _refusal = _best_verified_candidate(
        name, candidate_records, threshold, country_code=country_code,
        rejections=rejections,
        city=city, state=state, record_domain=record_domain,
    )
    if fields is None:
        logger.info(
            "GLEIF fuzzy: no verified candidate for '%s' (best score=%.1f)",
            name[:60], best_score,
        )
        return None

    logger.info(
        "GLEIF fuzzy matched '%s' → '%s' (LEI=%s, score=%.1f)",
        name[:60], fields["legal_name"], fields["lei_id"], best_score,
    )
    return {
        "matched": True,
        "strategy": "fuzzy",
        "confidence": "medium",
        "score": best_score,
        **fields,
    }


async def call_lei_by_id(
    lei: str,
    query_name: str,
    country_code: str | None = None,
    base_url: str = "https://api.gleif.org/api/v1",
    timeout: float = 15.0,
    max_retries: int = 2,
    threshold: float = 88.0,
    *,
    city: str | None = None,
    state: str | None = None,
    record_domain: str | None = None,
) -> dict[str, Any]:
    """Resolve a **known LEI** to its GLEIF record, with every guard applied.

    The [Wikidata crosswalk lane](`enrichment.wikidata`) is the only caller: a
    Wikidata item carrying ``P1278`` supplies a lookup key, and this follows it.
    The pointer is not the answer — the registry's response is — so the record
    GLEIF returns goes through :func:`_best_verified_candidate` exactly as a
    searched-for candidate does:

    * the **name-verification guard** scores ``legalName`` against *query_name*
      (the record's own Name 1) and rejects below *threshold*. This is what
      stops a wrong or stale Wikidata pointer from writing a different
      company's legal name into the customer master — the crowd-sourced link
      buys the lookup, the registry's own guard decides whether it stands;
    * the **country guard** rejects a record whose ``legalAddress.country``
      is not *country_code*.

    Neither guard is relaxed, and no new one is added. Returns the same result
    shape as :func:`call_lei` with ``strategy = "by_id"``. Never raises.
    """
    identifier = (lei or "").strip().upper()
    if not identifier:
        return {"matched": False, "strategy": "by_id", "score": 0.0}

    guard_rejections: list[dict[str, Any]] = []
    # Its own cache namespace — an LEI is not a name, and colliding the two
    # keyspaces would let a name lookup serve an identifier lookup.
    cache_key: CacheKey = (
        f"leiid:{identifier}", (country_code or "").strip().upper() or None,
    )
    if cache_key in _lei_cache:
        return _lei_cache[cache_key]
    def _cache_by_id(result: dict[str, Any]) -> dict[str, Any]:
        _lei_cache[cache_key] = result
        return result

    base = base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(
            timeout=timeout, verify=resolve_tls_verify(),
            headers={"Accept": _GLEIF_ACCEPT},
        ) as client:
            data = await _get_json(
                client, f"{base}/lei-records/{identifier}", None, max_retries,
            )
    except RegistryUnavailableFrozen:
        logger.info("GLEIF by-id: frozen cache has no response for %s", identifier)
        return {"matched": False, "strategy": "by_id", "score": 0.0,
                "guard_rejections": []}
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 404:
            # A pointer to an LEI GLEIF does not hold. A clean miss, not an
            # error: the crosswalk simply did not resolve.
            logger.info("GLEIF by-id: %s not found", identifier)
            return _cache_by_id(
                {"matched": False, "strategy": "by_id", "score": 0.0}
            )
        logger.error("GLEIF by-id HTTP %d for %s", status, identifier)
        return {"matched": False, "error": True}
    except Exception:
        logger.exception("GLEIF by-id lookup failed for %s", identifier)
        return {"matched": False, "error": True}

    record = data.get("data")
    records = [record] if isinstance(record, dict) else []
    fields, best_score, _refusal = _best_verified_candidate(
        query_name, records, threshold, country_code=country_code,
        rejections=guard_rejections,
        city=city, state=state, record_domain=record_domain,
    )
    if fields is None:
        logger.info(
            "GLEIF by-id: %s did not verify against '%s' (best score=%.1f)",
            identifier, (query_name or "")[:60], best_score,
        )
        return _cache_by_id({
            "matched": False, "strategy": "by_id", "score": best_score,
            "guard_rejections": guard_rejections,
        })

    result = {
        "matched": True,
        "strategy": "by_id",
        # An identifier the crosswalk supplied and the registry confirmed under
        # its own name guard is as good as the precise-filter path, and is
        # labelled the same way.
        "confidence": "high",
        "score": best_score,
        "guard_rejections": guard_rejections,
        **fields,
        # The crosswalk routed this by identifier. The name guard above did
        # run and did pass — but what chose the entity was a Wikidata pointer,
        # and a stale pointer is exactly how a record acquires another
        # company's LEI. Never exact tier, whatever the names happen to score.
        "name_match_tier": CROSSWALK_TIER,
    }
    logger.info(
        "GLEIF by-id matched %s → '%s' (score=%.1f)",
        identifier, fields["legal_name"], best_score,
    )
    return _cache_by_id(result)


class LEIClient:
    """Thin wrapper around call_lei() for dependency injection and mocking
    (mirrors RORClient)."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._base_url = settings.gleif_api_base
        self._timeout = settings.gleif_timeout_seconds
        self._max_retries = settings.lei_max_retries
        self._threshold = settings.lei_name_match_threshold

    async def call(
        self,
        name: str,
        country_code: str | None = None,
        *,
        city: str | None = None,
        state: str | None = None,
        record_domain: str | None = None,
    ) -> dict[str, Any]:
        """Look up a company name via GLEIF with an optional country filter.

        *city* / *state* / *record_domain* are the record's own context. GLEIF
        needs it for two rules it could not apply before: the registered
        locality check (Fix D2) and the corroborating signal a collision-prone
        name has to show (Fix C3). Omitting them is safe and only means those
        two rules have nothing to work with.
        """
        return await call_lei(
            name,
            country_code=country_code,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=self._max_retries,
            threshold=self._threshold,
            city=city,
            state=state,
            record_domain=record_domain,
        )

    async def call_by_id(
        self,
        lei: str,
        query_name: str,
        country_code: str | None = None,
        *,
        city: str | None = None,
        state: str | None = None,
        record_domain: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a known LEI, with the name and country guards unchanged."""
        return await call_lei_by_id(
            lei,
            query_name,
            country_code=country_code,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=self._max_retries,
            threshold=self._threshold,
            city=city,
            state=state,
            record_domain=record_domain,
        )
