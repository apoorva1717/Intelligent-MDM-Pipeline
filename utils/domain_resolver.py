"""Single write path for the ``domain`` / ``website_url`` fields.

Every value those two fields ever take is produced here. Callers hand in the
candidate URL they found plus whatever evidence the record carries; this module
canonicalises the URL and decides whether the resulting domain may be attributed
to the organisation at all.

Two separate concerns:

**Canonical form** — :func:`canonicalise_domain` reduces any candidate URL to the
registrable domain (scheme, ``www.``, path, query, fragment and trailing slash
removed; subdomains collapsed) by reusing
:func:`utils.text_utils.extract_domain`. ``website_url`` is then rebuilt as
``https://<domain>`` so it can never carry a deep path
(``…/home/index.en.html``) or a sub-site host (``investors.lockheedmartin.com``).
:func:`canonicalise_host` is the department-domain counterpart: it strips the
same URL parts but **keeps** the subdomain, because a department domain
legitimately *is* a subdomain (``chemistry.stanford.edu``).

**Ownership** — ROR has a country guard and GLEIF a name-verification guard,
because both upstream scorers return confident wrong answers. The domain path
had neither, so an unrelated company's website could be attached to a customer
record and read as successful enrichment (``delta.com`` for "Delta Analytical").
:func:`resolve_domain` closes that gap: a candidate is accepted only with
registry provenance, a name-similarity match, corroborating email evidence, or
on-domain search evidence. Otherwise ``domain`` is left empty and the caller
flags the record ``domain-unverified``.

Configuration: ``DOMAIN_NAME_MATCH_THRESHOLD`` (name-similarity cut-off) and
``DOMAIN_OWNERSHIP_GUARD_ENABLED`` (kill switch so the guard can be A/B
disabled), both on ``config.Settings`` — see the LEI_LOOKUP_ENABLED pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from rapidfuzz import fuzz

from enrichment.provenance import (
    DETERMINISTIC,
    FUZZY_RATIO,
    GUARD_DOMAIN_OWNERSHIP,
    REGISTRY_EXACT,
    Evidence,
)
from enrichment.tier1_ror import _normalise_for_tokens
from utils.text_utils import country_to_iso_code, extract_domain

# Flag code raised when a candidate domain fails every ownership condition.
DOMAIN_UNVERIFIED_CODE = "domain-unverified"
DOMAIN_UNVERIFIED_REASON = (
    "domain-unverified: website could not be verified as belonging to this "
    "organisation — verify"
)

# Free / consumer mailbox providers. An address at one of these says nothing
# about which organisation the record belongs to, so it can never corroborate a
# candidate domain. Matched on the registrable domain's LABEL so every country
# variant is covered (``yahoo.co.uk``, ``live.de``, …).
_GENERIC_EMAIL_LABELS: frozenset[str] = frozenset({
    "gmail", "googlemail", "outlook", "hotmail", "live", "yahoo", "ymail",
    "aol", "icloud", "me", "mac", "gmx", "protonmail", "proton", "mail",
    "msn", "zoho", "yandex", "fastmail", "hushmail", "inbox", "email",
})
# Providers whose label is too generic to blocklist on its own.
_GENERIC_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "web.de", "t-online.de", "freenet.de", "orange.fr", "wanadoo.fr",
    "libero.it", "bt.com", "comcast.net", "verizon.net", "sbcglobal.net",
})

_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_EMAIL_SPLIT_RE = re.compile(r"[;,\s]+")

# Minimum token length that counts as "significant" when checking whether a
# page title / H1 names the organisation. Mirrors the website resolver's
# name-overlap rule (utils are shared, the constant is not exported there).
_SIGNIFICANT_TOKEN_LEN = 4


# ---------------------------------------------------------------------------
# Canonical form
# ---------------------------------------------------------------------------

def _with_scheme(url: str) -> str:
    """``extract_domain`` parses via urlparse, which only exposes a hostname
    when a scheme is present — so a bare host ('lockheedmartin.com') would
    otherwise parse as a path. Add one when it is missing."""
    url = url.strip()
    if not url:
        return url
    if _SCHEME_RE.match(url):
        return url
    return f"https://{url}"


def canonicalise_domain(url: str | None) -> str | None:
    """Registrable domain for *url* — the value the ``domain`` field carries.

    Strips scheme, ``www.``, path, query, fragment and any trailing slash, then
    collapses subdomains via :func:`utils.text_utils.extract_domain`::

        http://www.uni-stuttgart.de/home/index.en.html  → uni-stuttgart.de
        https://investors.lockheedmartin.com            → lockheedmartin.com
        https://www.example.co.uk/page?q=1#top          → example.co.uk

    Accepts a bare host as well as a full URL. Returns ``None`` when nothing
    usable is left.
    """
    if not url or not url.strip():
        return None
    domain = extract_domain(_with_scheme(url))
    if not domain:
        return None
    domain = domain.strip().lower().rstrip(".")
    return domain or None


def canonicalise_host(url: str | None) -> str | None:
    """Full host for *url*, **without** collapsing subdomains — the
    ``department_domain`` counterpart to :func:`canonicalise_domain`.

    Department domains legitimately *are* subdomains (``chemistry.stanford.edu``,
    ``be.mit.edu``), so collapsing them would destroy the Tier 2B output. Only
    the path / query / fragment / trailing slash and a leading ``www.`` are
    removed::

        https://medschool.umich.edu/departments/radiation-oncology
            → medschool.umich.edu
    """
    if not url or not url.strip():
        return None
    try:
        host = (urlparse(_with_scheme(url)).hostname or "").strip().lower()
    except Exception:
        return None
    host = host.rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host or None


def website_url_for(domain: str | None) -> str | None:
    """The homepage URL emitted alongside an accepted *domain*."""
    return f"https://{domain}" if domain else None


def domain_label(domain: str | None) -> str | None:
    """The registrable label — everything before the public suffix.

    ``uni-stuttgart.de`` → ``uni-stuttgart``; ``example.co.uk`` → ``example``.
    """
    if not domain:
        return None
    head = domain.split(".")[0]
    return head or None


# ---------------------------------------------------------------------------
# Country gate
# ---------------------------------------------------------------------------

# A TLD of exactly two letters is a ccTLD, and the ccTLD string IS the ISO
# 3166-1 alpha-2 code of the country it belongs to. ICANN has never delegated a
# two-character gTLD, so the country a domain claims is derivable without a
# 249-entry table: anything longer (`.com`, `.org`, `.pharmacy`, an IDN's
# `xn--…` form) is country-neutral by construction and is never gated.
#
# Why gate at all: the SERP layer validates a candidate on a name token in the
# host, and a multinational's name matches its site in every country it
# operates in. "Unilever Trumbull Research Services Inc" (US, TX) matched
# `unilever.be` on the token "unilever" — a real Unilever site, in the wrong
# country, for a Connecticut research subsidiary. Nothing downstream compared
# the two, because nothing downstream knew the record had a country.

#: ccTLDs sold and used worldwide, whose letters no longer say anything about
#: where an organisation sits. `.io` is the British Indian Ocean Territory and
#: `.ai` is Anguilla on paper; rejecting a US company's `.ai` site as foreign
#: would be a false positive on one of the most common TLDs it could pick.
_GENERIC_USE_CCTLDS: frozenset[str] = frozenset({
    "io", "ai", "co", "me", "tv", "cc", "ly", "sh", "gg", "fm", "to",
    "ac", "su", "st", "am", "im",
})

#: ccTLDs whose letters differ from the ISO code of the country they serve.
#: `.uk` is the common one — ISO 3166-1 assigns GB, which is also what
#: :func:`utils.text_utils.country_to_iso_code` normalises "UK", "England" and
#: "Scotland" to, so the two sides have to be brought onto the same code
#: before they can be compared at all.
_CCTLD_TO_ISO: dict[str, str] = {"uk": "GB"}

#: `.eu` is supranational: not neutral — a US organisation has no claim to one
#: — but it does not name a single country either, so it is read as "any EU
#: member state" and conflicts only with a record outside the union.
_EU_MEMBER_STATES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
})


def domain_tld(domain: str | None) -> str | None:
    """The last label of *domain* — `uni-stuttgart.de` → `de`, `example.co.uk`
    → `uk`. Callers pass a registrable domain, so the public suffix's own last
    label is what comes back, which is exactly the ccTLD when there is one."""
    if not domain:
        return None
    tld = domain.rsplit(".", 1)[-1].strip().lower()
    return tld or None


def country_conflict(domain: str | None, country: str | None) -> str | None:
    """The country a candidate domain's ccTLD claims, when that CONTRADICTS
    the record's own country. ``None`` when the two agree or when neither side
    makes a claim.

    Fails open on every unknown, and deliberately so — this rejects candidates,
    so an unrecognised country string or an unfamiliar TLD must not manufacture
    a conflict::

        country_conflict("unilever.be", "US")  → "BE"    (a US record)
        country_conflict("unilever.com", "US") → None    (gTLD — neutral)
        country_conflict("basf.de", "DE")      → None    (agrees)
        country_conflict("example.ai", "US")   → None    (worldwide ccTLD)
        country_conflict("example.eu", "US")   → "EU"    (outside the union)
        country_conflict("example.eu", "BE")   → None    (a member state)
        country_conflict("unilever.be", None)  → None    (nothing to contradict)
    """
    tld = domain_tld(domain)
    if not tld or len(tld) != 2 or tld in _GENERIC_USE_CCTLDS:
        return None
    record_iso = country_to_iso_code(country)
    if not record_iso:
        # The record does not state a country this module can read. There is
        # no claim to contradict, so there is no conflict to report.
        return None
    if tld == "eu":
        return None if record_iso in _EU_MEMBER_STATES else "EU"
    claimed = _CCTLD_TO_ISO.get(tld, tld.upper())
    return None if claimed == record_iso else claimed


# ---------------------------------------------------------------------------
# Evidence / decision types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DomainEvidence:
    """What the caller knows about the record and where the candidate came from.

    Parameters
    ----------
    name1
        The organisation name the domain is claimed to belong to.
    email
        The record's email address, if any (used for condition 3).
    registry
        ``"ROR"`` or ``"GLEIF"`` when the candidate came out of a registry
        record that already passed that registry's own guard (ROR's country
        guard / GLEIF's name verification). ``None`` for web-derived candidates.
    serp_title, serp_h1
        Title / H1 of the search result the candidate came from (condition 4).
    serp_url
        URL of that search result — the on-domain check requires it to be on the
        candidate's own domain.
    stated_websites
        ``(witness, url)`` pairs: every official website an INDEPENDENT
        identity system states for the organisation this record was resolved
        to — ROR's ``links[]`` entries, Wikidata's ``P856`` claims. *witness*
        is the provenance witness token that system earns
        (``"registry"`` / ``"wikidata"``). Evidence the pipeline already
        fetched while resolving the identity; nothing here goes to the
        network.
    page_identity
        The page served BY the candidate domain states this organisation's
        own name (condition 5). Unlike the four above this arrives late — the
        page read runs after the guard has judged the candidate — so it is
        passed on a second call rather than being available on the first.
    country
        The record's own country, in any form ``country_to_iso_code`` reads.
        Not an ownership condition — a DISQUALIFIER: a candidate whose ccTLD
        places it in a different country cannot be this record's site, so it
        is refused before the scored conditions get to argue for it
        (:func:`country_conflict`). Left ``None`` to disable the gate for this
        call, which is how ``DOMAIN_COUNTRY_GATE_ENABLED=false`` restores the
        previous behaviour exactly.
    """
    name1: str | None = None
    email: str | None = None
    registry: str | None = None
    serp_title: str | None = None
    serp_h1: str | None = None
    serp_url: str | None = None
    stated_websites: tuple[tuple[str, str], ...] = ()
    page_identity: bool = False
    country: str | None = None


@dataclass(frozen=True)
class DomainDecision:
    """Outcome of one :func:`resolve_domain` call.

    ``domain`` / ``website_url`` are what the caller writes (both ``None`` on a
    rejection). ``verified_by`` names the ownership condition that carried it:
    ``registry`` | ``witness_registry`` | ``witness_wikidata`` | ``name`` |
    ``email`` | ``serp`` | ``page`` — or ``unguarded`` when
    ``DOMAIN_OWNERSHIP_GUARD_ENABLED`` is off.

    ``witness`` names the independent system whose stated official website
    matched, on the two ``witness_*`` conditions only. It is what separates
    ``web:{domain}:verified+wikidata`` from ``web:{domain}:verified+registry``
    downstream, so the decision carries it rather than the caller re-deriving
    it.

    ``rejected_by`` is the mirror of ``verified_by`` on the failing side:
    ``conditions`` when every ownership condition simply came up short, or
    ``country`` when the candidate was disqualified before they were consulted.
    The two are not the same outcome and must not read as one — a candidate the
    guard could not tie to the record is worth a reviewer's look, and a
    candidate in the wrong country is not.
    """
    domain: str | None = None
    website_url: str | None = None
    verified_by: str | None = None
    rejected: bool = False
    rejected_by: str | None = None
    candidate: str | None = None
    witness: str | None = None

    @property
    def accepted(self) -> bool:
        return self.domain is not None


# ---------------------------------------------------------------------------
# Ownership conditions
# ---------------------------------------------------------------------------

def email_domain(email: str | None) -> str | None:
    """Registrable domain of *email*, or ``None`` when absent or generic.

    A record may carry several addresses; the first one with a usable,
    non-generic domain wins.
    """
    if not email or not email.strip():
        return None
    for part in _EMAIL_SPLIT_RE.split(email.strip()):
        if "@" not in part:
            continue
        host = part.rsplit("@", 1)[-1].strip().strip("<>()[]").rstrip(".")
        domain = canonicalise_domain(host)
        if not domain or is_generic_email_domain(domain):
            continue
        return domain
    return None


def is_generic_email_domain(domain: str | None) -> bool:
    """True for consumer mailbox providers (gmail, web.de, …)."""
    if not domain:
        return True
    if domain in _GENERIC_EMAIL_DOMAINS:
        return True
    return (domain_label(domain) or "") in _GENERIC_EMAIL_LABELS


def name_similarity(name1: str | None, domain: str | None) -> float:
    """RapidFuzz ``token_sort_ratio`` between Name 1 and the domain label.

    Name 1 is normalised with the existing
    :func:`enrichment.tier1_ror._normalise_for_tokens` (dashes and punctuation
    to spaces, legal suffixes canonicalised). The domain label is split on
    hyphens only — concatenated words are deliberately **not** segmented, since
    guessing word boundaries inside ``aumbiotech`` produces false confidence.
    """
    label = domain_label(domain)
    if not name1 or not name1.strip() or not label:
        return 0.0
    name_side = _normalise_for_tokens(name1)
    if not name_side:
        return 0.0
    return float(fuzz.token_sort_ratio(name_side, label.replace("-", " ")))


def _significant_tokens(name1: str | None) -> set[str]:
    if not name1:
        return set()
    return {
        tok for tok in _normalise_for_tokens(name1).split()
        if len(tok) >= _SIGNIFICANT_TOKEN_LEN
    }


def stated_website_witness(
    evidence: DomainEvidence, domain: str | None,
) -> str | None:
    """Condition 1b — an INDEPENDENT system states this very domain.

    Returns the witness token (``"registry"`` / ``"wikidata"``) of the first
    stated official website whose registrable domain equals the candidate's,
    or ``None``.

    The comparison is on the registrable stem, through the same
    :func:`canonicalise_domain` every other value in this module goes through,
    so ``https://www.jnj.com/`` stated against a candidate ``jnj.com`` is an
    agreement and not a string difference. That is the whole condition: the
    registry or knowledge base that identified the organisation names the same
    website the web path found, and two systems that never consulted each
    other agreeing is exactly what a witness IS.

    Why this outranks the name-similarity condition: name similarity is one
    string comparison against a domain label it cannot segment, and it is the
    condition this module documents as carrying the least weight. An
    independent statement of the official website is not a similarity at all —
    it is a second source, and provenance says so (``verified+witness`` rather
    than ``provisional``).
    """
    if not domain:
        return None
    for witness, url in evidence.stated_websites or ():
        if canonicalise_domain(url) == domain:
            return witness or None
    return None


def has_on_domain_evidence(evidence: DomainEvidence, domain: str | None) -> bool:
    """Condition 4 — the candidate came from a search result **on that domain**
    whose page title or H1 names the organisation.

    Every significant Name-1 token must appear, not just one: the SERP layer
    already admits a result on a single ≥4-char overlap, which is exactly how a
    stranger's page ("… Biotech …") slips through.
    """
    if not domain:
        return False
    if canonicalise_domain(evidence.serp_url) != domain:
        return False
    haystack = " ".join(
        part for part in (evidence.serp_title, evidence.serp_h1) if part
    ).lower()
    if not haystack.strip():
        return False
    tokens = _significant_tokens(evidence.name1)
    if not tokens:
        return False
    return all(tok in haystack for tok in tokens)


# ---------------------------------------------------------------------------
# The chokepoint
# ---------------------------------------------------------------------------

def _settings_defaults(
    threshold: float | None,
    guard_enabled: bool | None,
    country_gate_enabled: bool | None = None,
):
    if (
        threshold is not None
        and guard_enabled is not None
        and country_gate_enabled is not None
    ):
        return threshold, guard_enabled, country_gate_enabled
    from config import get_settings  # local import — avoids an import cycle
    settings = get_settings()
    if threshold is None:
        threshold = settings.domain_name_match_threshold
    if guard_enabled is None:
        guard_enabled = settings.domain_ownership_guard_enabled
    if country_gate_enabled is None:
        country_gate_enabled = settings.domain_country_gate_enabled
    return threshold, guard_enabled, country_gate_enabled


def resolve_domain(
    candidate_url: str | None,
    evidence: DomainEvidence | None = None,
    *,
    threshold: float | None = None,
    guard_enabled: bool | None = None,
    country_gate_enabled: bool | None = None,
) -> DomainDecision:
    """Canonicalise *candidate_url* and decide whether it may be attributed to
    this organisation. The only place ``domain`` / ``website_url`` are decided.

    Before any of that, a **country disqualifier**: when the candidate's ccTLD
    places it in a country other than the record's (:func:`country_conflict`),
    the scored conditions are not consulted at all. Registry provenance and an
    independent witness are exempt — those are an authoritative source SAYING
    this is the organisation's site, and a US-registered subsidiary whose
    registry record names a ``.de`` site is stating a fact, not guessing. The
    email condition is exempt too, because it does not attribute the candidate:
    it replaces the candidate with a domain the record itself carries.

    Precedence, first hit wins:

    1. **Registry provenance** — the candidate came from a ROR record that
       passed the country guard, or a GLEIF record that passed name
       verification. Sufficient on its own.
    1b. **An independent witness states this website** — the ROR record or the
       Wikidata item the record was resolved to publishes an official website
       whose registrable domain IS the candidate's
       (:func:`stated_website_witness`). Two systems agreeing, so the value
       reaches ``verified`` with the witness named. Ranked directly below
       registry provenance and above every scored condition: it is a second
       source, not a better similarity.
    2. **Name similarity** — ``token_sort_ratio`` at or above the threshold.
       Checked before the email so a well-matched candidate is never clobbered
       by an unrelated address on the record (a distributor's mailbox).
    3. **Email evidence** — the record carries a non-generic email domain. When
       the candidate could not be verified on its own, this *replaces* it: a
       record holding ``ORDERS@MERIDIANLABS.COM`` already knows the
       organisation's domain better than a search result does
       (``meridianlabs.ai``).
    4. **On-domain search evidence** — the candidate's own page names the org.
    5. **Page identity** — the page served BY the candidate domain states this
       organisation's name and does not place it in a different state or
       country (``evidence.page_identity``, decided by
       :mod:`enrichment.page_corroborator`). Last, and deliberately: a page
       fetched from the domain it vouches for is ONE source, not two, so it
       accepts at ``provisional`` and never at ``verified``. It is still the
       best evidence available about a candidate that reached none of the four
       above — the site itself, asked directly — and refusing to consult it
       while flagging the record "verify this domain" was asking a reviewer to
       do by hand the one check the pipeline had declined to do.

    None of the above → ``domain`` and ``website_url`` stay empty and
    ``rejected`` is set, so the caller can raise ``domain-unverified``.
    ``rejected_by`` separates the two ways that happens (``conditions`` vs
    ``country``), because only one of them leaves a domain worth a reviewer's
    time.
    Attaching an unrelated company's website is worse than an empty field,
    because it reads as successful enrichment.
    """
    evidence = evidence or DomainEvidence()
    candidate = canonicalise_domain(candidate_url)
    if not candidate:
        return DomainDecision()

    threshold, guard_enabled, country_gate_enabled = _settings_defaults(
        threshold, guard_enabled, country_gate_enabled,
    )

    def accept(
        domain: str, verified_by: str, *, witness: str | None = None,
    ) -> DomainDecision:
        return DomainDecision(
            domain=domain,
            website_url=website_url_for(domain),
            verified_by=verified_by,
            candidate=candidate,
            witness=witness,
        )

    if evidence.registry:
        return accept(candidate, "registry")

    if not guard_enabled:
        # A/B kill switch: canonicalisation still applies, ownership does not.
        return accept(candidate, "unguarded")

    witness = stated_website_witness(evidence, candidate)
    if witness:
        return accept(candidate, f"witness_{witness}", witness=witness)

    # The country disqualifier. Everything below this line reasons about the
    # CANDIDATE, and a candidate whose ccTLD sits in another country is not
    # this record's site however well its label scores — which is the whole
    # failure: a multinational's name matches its site in every country it
    # operates in, so name similarity is the one test that cannot catch this.
    conflict = (
        country_conflict(candidate, evidence.country)
        if country_gate_enabled else None
    )

    if not conflict and name_similarity(evidence.name1, candidate) >= threshold:
        return accept(candidate, "name")

    # Exempt from the disqualifier: this does not attribute the candidate at
    # all, it discards it in favour of a domain the record already carries.
    from_email = email_domain(evidence.email)
    if from_email:
        return accept(from_email, "email")

    if not conflict and has_on_domain_evidence(evidence, candidate):
        return accept(candidate, "serp")

    if not conflict and evidence.page_identity:
        return accept(candidate, "page")

    return DomainDecision(
        rejected=True,
        rejected_by="country" if conflict else "conditions",
        candidate=candidate,
    )


# ---------------------------------------------------------------------------
# The write (Fix 10)
# ---------------------------------------------------------------------------

#: How each ownership condition is attributed. The condition that accepted the
#: candidate IS its provenance: "registry" means ROR/GLEIF vouched for it,
#: "name" means a scored string comparison did, and those are not the same
#: claim — which is exactly why the scale travels with the value.
_VERIFIED_BY_SCALE: dict[str, str] = {
    "registry": REGISTRY_EXACT,
    # A registry or knowledge base STATED this website. The claim is an
    # identifier-grade one — an equality between two canonicalised domains,
    # not a ratio — which is what `REGISTRY_EXACT` means here as well.
    "witness_registry": REGISTRY_EXACT,
    "witness_wikidata": REGISTRY_EXACT,
    "name": FUZZY_RATIO,
    "email": DETERMINISTIC,
    "serp": DETERMINISTIC,
    # The page read's own name comparison. It is a ratio-or-containment
    # answer, and the score the corroborator measured travels on the
    # provenance event's `evidence_ref` rather than in this scale.
    "page": DETERMINISTIC,
    "unguarded": DETERMINISTIC,
}


def write_domain(
    record,
    candidate_url: str | None,
    evidence: DomainEvidence,
    *,
    producer_chain: tuple[str, ...] = ("website_resolver",),
    registry_identifier: str | None = None,
    threshold: float | None = None,
    guard_enabled: bool | None = None,
    country_gate_enabled: bool | None = None,
    tier: int | None = None,
) -> DomainDecision:
    """Decide a candidate domain and write it through ``record.write``.

    This is Fix 1's chokepoint, now attributing what it writes rather than
    running as a parallel mechanism: :func:`resolve_domain` still takes the
    decision, and the decision's ``verified_by`` — the ownership condition that
    carried it — becomes the provenance of the value. A candidate the guard
    refuses is recorded as a guard rejection (Step 4) instead of vanishing: the
    pipeline had a confident answer and deliberately declined it, which is the
    case most worth being able to defend afterwards.

    ``record`` is an :class:`enrichment.provenance.EnrichedRecord`. Everything
    other than ``domain`` (``website_url``, the transient raw candidate, the
    verified-by telemetry) is unscoped and written directly, exactly as before.
    """
    decision = resolve_domain(
        candidate_url, evidence,
        threshold=threshold, guard_enabled=guard_enabled,
        country_gate_enabled=country_gate_enabled,
    )

    if decision.domain:
        scale = _VERIFIED_BY_SCALE.get(decision.verified_by or "", DETERMINISTIC)
        if scale == FUZZY_RATIO:
            value = name_similarity(evidence.name1, decision.domain)
        elif scale == REGISTRY_EXACT:
            value = 1.0
        else:
            value = 1.0
        ref: dict[str, object] = {
            "source_url": candidate_url,
            "verified_by": decision.verified_by,
        }
        if decision.witness:
            # WHICH independent system agreed. `situation_for` reads this to
            # pick `+registry` or `+wikidata`; a reviewer reads it to know
            # who to go and ask.
            ref["witness"] = decision.witness
            ref["witness_url"] = next(
                (
                    url for w, url in (evidence.stated_websites or ())
                    if w == decision.witness
                    and canonicalise_domain(url) == decision.domain
                ),
                None,
            )
        if registry_identifier:
            ref["registry_id"] = registry_identifier
        if decision.verified_by == "email":
            # The domain came off the record's own address, not the candidate.
            ref["source_url"] = None
            ref["email_domain"] = decision.domain
        record.write(
            "domain", decision.domain,
            Evidence(
                producer_chain=(
                    (evidence.registry.lower(),)
                    if decision.verified_by == "registry" and evidence.registry
                    else ("record_email",)
                    if decision.verified_by == "email"
                    else producer_chain
                ),
                tier=tier,
                confidence_scale=scale,
                confidence_value=value,
                evidence_ref=ref,
                rule_id=f"domain-ownership:{decision.verified_by}",
            ),
        )
        record["website_url"] = decision.website_url
        record["_website_raw"] = candidate_url
        record["domain_verified_by"] = decision.verified_by
        # A later, verified candidate clears an earlier rejection.
        record["domain_rejected"] = False
        record.pop("_domain_unverified", None)
    elif decision.rejected:
        record["domain_rejected"] = True
        by_country = decision.rejected_by == "country"
        conflict = (
            country_conflict(decision.candidate, evidence.country)
            if by_country else None
        )
        # The rejected candidate itself, not a bare marker: the flag reason
        # names the domain a reviewer has to go and confirm, and this is the
        # only place that still knows which one it was.
        #
        # A country rejection raises NO flag. `domain-unverified` means "a
        # candidate was found and a human has to decide about it", and there is
        # nothing here to decide: the candidate is in a different country,
        # which is a fact, not a doubt. Telling a reviewer to "confirm
        # unilever.be before using it" for a Texas record asks them to redo work
        # the pipeline has already finished, and names a domain that must not be
        # used in a sentence that reads as a suggestion — which is how this was
        # found. The rejection is still recorded in full below, so the decision
        # is auditable without being pushed at a person.
        if not by_country:
            record["_domain_unverified"] = decision.candidate or True
        record.reject(
            "domain", decision.candidate, GUARD_DOMAIN_OWNERSHIP,
            reason=(
                f"candidate is in a different country: its ccTLD places "
                f"{decision.candidate} in {conflict}, the record is in "
                f"{country_to_iso_code(evidence.country)}"
                if by_country else
                "no ownership condition held: not from a registry, name "
                "similarity below threshold, no non-generic email domain, "
                "and no on-domain search evidence"
            ),
            evidence=Evidence(
                producer_chain=producer_chain,
                tier=tier,
                # A country rejection is not a scored one — nothing was
                # measured and fell short, the candidate was disqualified — so
                # it is filed as the deterministic fact it is rather than
                # carrying a similarity ratio that had no part in the outcome.
                confidence_scale=DETERMINISTIC if by_country else FUZZY_RATIO,
                confidence_value=(
                    1.0 if by_country
                    else name_similarity(evidence.name1, decision.candidate)
                ),
                evidence_ref={
                    "source_url": candidate_url,
                    "claimed_for": evidence.name1,
                    **(
                        {
                            "rejected_by": "country",
                            "domain_country": conflict,
                            "record_country": country_to_iso_code(
                                evidence.country,
                            ),
                        }
                        if by_country else {}
                    ),
                },
                rule_id=(
                    "domain-country-gate" if by_country
                    else "domain-ownership-guard"
                ),
            ),
        )
    return decision
