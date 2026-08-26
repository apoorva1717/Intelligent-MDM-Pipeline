"""The evidence cache: one directory, several namespaces, keyed on the request.

Every external answer the pipeline reads — a SERP page, a fetched web page, a
Wikidata item, a ROR or GLEIF lookup — is recorded here under a key that is a
pure function of the request. Two things follow, and Fix B exists for both:

**A re-run reproduces the evidence, it does not re-gather it.** Two runs of the
identical 101-row chemspeed batch on the identical codebase produced 7
substantively different records. Eleven of the differing rows differed only in
an extraction date, which is what a cache that does not survive the process
looks like from the outside: every fetch re-executed, and a re-executed fetch
can return something else. A cache that lives only in memory is not a
reproducibility mechanism; a cache on disk, shared by every namespace, is.

**A frozen cache is an evaluation control.** :data:`CACHE_FROZEN` turns a miss
into a recorded error rather than a network call — the analogue of freezing
``dedup/weights.json`` before an evaluation. The record proceeds without that
piece of evidence and the miss is traced as ``evidence-unavailable-frozen``,
so a thesis measurement can state exactly which records were short of evidence
instead of silently re-gathering it against a web that has moved on.

Namespaces
----------

One subdirectory of :data:`EVIDENCE_CACHE_DIR` per source, one JSON file per
key inside it:

===============  ==================  =====================================
Namespace        Directory           Key
===============  ==================  =====================================
``page``         ``page_reads/``     registrable domain
``wikidata``     ``wikidata/``       ``search:<normalised query>`` / ``entity:<QID>``
``serp``         ``serp/``           normalised query + quoted-flag + country
``fetch``        ``fetch/``          the URL (or host) the request was made to
``llm``          ``llm/``            digest of deployment + sampling params + both prompts
``ror``          ``registry/``       normalised name + country
``gleif``        ``registry/``       normalised name + country
===============  ==================  =====================================

Cache keys
----------

Every key is built here, from the request and nothing else. **No key contains
a run id, a batch id, a date or a record id** — that is the property that makes
a second run hit rather than miss, and ``tests/test_determinism.py`` asserts it
structurally rather than by inspection.

Every namespace keys on a *normalised* form of the query plus the country,
built by :func:`lookup_key` / :func:`serp_key`. Lowercasing alone collapses
"MIT" and "mit" but not ``Coastal Diagnostics, Inc.`` against
``Coastal Diagnostics Inc``, so within one batch the same organisation was
looked up under several keys, got several outcomes, and the batch emitted
contradictory records for one entity.

The normaliser is :func:`dedup.signatures.normalize_key` — reused rather than
reimplemented: lowercase, trim, collapse whitespace, strip punctuation, fold
accents (``Universität`` → ``universitat``). It deliberately does **not** strip
legal forms or expand abbreviations, which is the right conservatism for a
cache key too.

Three conditions hold for every key built here, and the first is what keeps the
punctuation stripping safe:

1. The normalised key is used ONLY as a dictionary key for cache lookup.
2. The value SENT to the ROR / LEI / SERP APIs is always the original,
   unnormalised string. The key decides *whether* a call is made; it is never
   the payload. Pinned by ``tests/test_cache_normalisation.py``.
3. The key never reaches output, never reaches an LLM prompt, and never enters
   ``_compute_name_score()`` or any other scoring path.

Country is part of every key: a name-only key lets two genuinely distinct
organisations that share a name in different countries share a cache entry.
(It does not separate a same-country name collision — two US "Cardinal
Instruments" still collide, which is out of scope here.)

Entries are immutable
---------------------

An entry is written once, by the live fetch that produced it, and carries the
UTC date of that fetch in ``fetched_at``. Nothing rewrites an existing entry:
:meth:`DiskCache.set` is a no-op when the key is already recorded. That is what
makes ``Operating Name Provenance`` reproducible — the date in it is the date
the page was *read*, taken from the entry, not the date the run executed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

from dedup.signatures import normalize_key
from search.base import SearchUnavailable
from utils.text_utils import country_to_iso_code

logger = logging.getLogger(__name__)

#: One JSON line per frozen-mode miss, on its own logger — the same shape as
#: ``enrichment.trace.page`` / ``enrichment.trace.retry``. A frozen miss is a
#: statement about a record ("this record shipped without the evidence it
#: would normally have had"), so it has to be recoverable as data.
trace_logger = logging.getLogger("enrichment.trace.cache")

#: The trace ``step`` for a miss under :data:`CACHE_FROZEN`.
FROZEN_MISS = "evidence-unavailable-frozen"

#: The record currently being enriched, set by ``Orchestrator._enrich_single``.
#: A ContextVar rather than a parameter threaded through eight call sites:
#: asyncio gives each concurrently-enriched record its own value for free, and
#: the alternative is a ``record_id`` argument on every cache read in the
#: codebase for the sake of one diagnostic line.
current_record_id: ContextVar[Optional[str]] = ContextVar(
    "current_record_id", default=None,
)

# Key type shared by the ROR and LEI namespaces. Spelled with typing.Tuple
# because this alias is evaluated at runtime (the `annotations` future import
# only defers real annotations).
CacheKey = Tuple[str, Optional[str]]


def _country_part(country_code: str | None) -> str | None:
    return (country_code or "").strip().upper() or None


def lookup_key(name: str | None, country_code: str | None = None) -> CacheKey:
    """Cache key for a registry (ROR / LEI) lookup of *name* in *country_code*.

    INTERNAL CACHE KEY ONLY — see the module docstring. The unnormalised
    *name* is what actually reaches the API.
    """
    return (normalize_key(name), _country_part(country_code))


def legacy_lookup_key(name: str | None, country_code: str | None = None) -> CacheKey:
    """The pre-fix key (lowercase + strip only).

    Kept solely to measure ``cache_hits_after_normalisation`` — how many
    lookups the normalised key saved that the lowercased key would have
    missed. Never used to store or retrieve a value.
    """
    return ((name or "").strip().lower(), _country_part(country_code))


def serp_key(query: str, country: str | None = None) -> tuple[str, bool, str | None]:
    """Cache key for a SERP query.

    Same normalisation as :func:`lookup_key`, plus one extra component: whether
    the query carried a quoted exact phrase. ``normalize_key`` strips the quote
    characters, which would make an exact-phrase query and its unquoted retry
    (website_resolver §8) collide — the retry would be served the very results
    it exists to get away from. Quoting changes what is searched, so it is part
    of the identity of the query rather than noise to fold away.
    """
    return (normalize_key(query), '"' in (query or ""), _country_part(country))


def legacy_serp_key(query: str, country: str | None = None) -> tuple[str, bool, str | None]:
    """The pre-fix SERP key. Measurement only — see :func:`legacy_lookup_key`."""
    return ((query or "").strip().lower(), '"' in (query or ""), _country_part(country))


def serp_disk_key(query: str, country: str | None = None) -> str:
    """:func:`serp_key` rendered as the flat string the disk store keys on."""
    normalised, quoted, country_part = serp_key(query, country)
    return f"serp:{country_part or '-'}:{'q' if quoted else 'u'}:{normalised}"


def http_disk_key(url: str, params: "dict[str, str] | None" = None) -> str:
    """Cache key for one HTTP GET: the URL plus its query parameters.

    Parameters are sorted, so two requests that differ only in the order a
    client happened to build them in share an entry. The key is readable up to
    the store's own sanitisation, which appends a digest when it has to
    substitute characters — a registry URL is full of them.
    """
    if params:
        query = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{url}?{query}"
    return url


def llm_disk_key(
    *,
    deployment: str,
    api_version: str,
    temperature: float,
    top_p: float,
    seed: int | None,
    max_tokens: int,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Cache key for one chat completion.

    A digest, because the prompts run to thousands of characters and a
    filename cannot carry them. Everything that can change the answer is in
    it — the deployment, the API version, all three sampling parameters, the
    token budget and both prompts verbatim — so an entry can only ever be
    served to a request that is identical in every respect. Editing a prompt
    template invalidates every entry that used it, which is correct: the
    recorded answer was an answer to a different question.

    Deliberately NOT the ``prompt_version`` digest the provenance log records.
    That identifies the TEMPLATE; this has to identify the rendered prompt,
    because two records put different values through the same template.
    """
    material = "␟".join([
        deployment, api_version, f"{temperature!r}", f"{top_p!r}",
        "none" if seed is None else str(seed), str(max_tokens),
        system_prompt, user_prompt,
    ])
    return "llm:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The disk store
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class DiskCache:
    """Process-level in-memory cache with an on-disk fixture store behind it.

    Two layers, in this order:

    1. **In memory**, for the life of the orchestrator — two records naming the
       same organisation cost one fetch.
    2. **On disk**, one JSON file per key under *fixture_dir* — the layer that
       survives the process. It is what makes a re-run of a thesis batch
       reproduce its decisions: the second run reads what the first run
       actually saw, not whatever the source serves now. Recording is a pure
       side effect of a live fetch; nothing is fetched to populate it.

    ``replay_only`` refuses to fetch anything that is not already recorded —
    :data:`CACHE_FROZEN`'s mechanism, and the mode a CI run or an offline
    re-analysis wants, where a missing entry should surface as a recorded
    unavailability rather than as a silent new network call. The caller
    enforces it; this class reports the miss (:data:`FROZEN_MISS`) and counts
    it.

    Payloads are plain JSON values; this class does not interpret them. The key
    is an opaque, case-folded string — a registrable domain for the page reads
    this was written for, ``entity:<QID>`` for Wikidata, a rendered query for
    SERP, a rendered name+country for the registries.

    Entries are immutable: :meth:`set` will not overwrite a key that is already
    on disk, so ``fetched_at`` keeps naming the day the evidence was actually
    gathered however many times the batch is re-run.
    """

    def __init__(
        self,
        fixture_dir: "str | Path | None" = None,
        *,
        replay_only: bool = False,
        prefix: str = "page",
        namespace: str | None = None,
    ) -> None:
        self._store: dict[str, Any] = {}
        self._fetched_at: dict[str, str] = {}
        self._dir = Path(fixture_dir) if fixture_dir else None
        self.replay_only = replay_only
        # Filename prefix for the on-disk fixtures. `page` for the page reads
        # this class was written for; every other namespace uses its own, so
        # the fixture sets stay legible side by side instead of one borrowing
        # the other's naming. It is a filename detail only — the key itself,
        # and the envelope the key is verified against, are unchanged.
        self._prefix = prefix
        self.namespace = namespace or prefix
        self.disk_hits = 0
        self.memory_hits = 0
        self.recorded = 0
        #: Keys a frozen run wanted and did not have. Counted for the batch
        #: summary; each one is also traced at the moment it happens.
        self.frozen_misses = 0

    # -- keys and paths ------------------------------------------------------

    @staticmethod
    def _key(domain: str | None) -> str:
        return (domain or "").strip().lower().rstrip(".")

    def _path(self, key: str) -> "Path":
        # The key itself, sanitised — a fixture directory a human can read is
        # worth more here than an opaque digest, and the file records the key
        # it was written under so a sanitisation collision cannot be missed.
        # When sanitising actually changed something (a long SERP query, a
        # character outside the safe set) an 8-char digest of the full key is
        # appended, so two different queries cannot land on one filename. A
        # plain domain sanitises to itself and keeps its historical filename.
        safe = re.sub(r"[^a-z0-9._-]", "_", key)[:100]
        if safe != key:
            digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
            safe = f"{safe}-{digest}"
        return self._dir / f"{self._prefix}_{safe}.json"

    # -- reads ---------------------------------------------------------------

    def get_entry(self, domain: str | None) -> dict[str, Any] | None:
        """``{"payload", "fetched_at"}`` for *domain*, or None on a miss.

        ``fetched_at`` is the ISO date the evidence was gathered. For an entry
        written before this field existed it falls back to the file's own
        modification date — which is still the day the fetch happened, and
        still stable across re-runs, so a legacy fixture reproduces its
        provenance string rather than acquiring today's date.
        """
        key = self._key(domain)
        if not key:
            return None
        if key in self._store:
            self.memory_hits += 1
            return {
                "payload": self._store[key],
                "fetched_at": self._fetched_at.get(key),
            }
        if self._dir is None:
            self._note_frozen_miss(key)
            return None
        path = self._path(key)
        if not path.is_file():
            self._note_frozen_miss(key)
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt fixture is a miss
            self._note_frozen_miss(key)
            return None
        if raw.get("domain") != key:
            self._note_frozen_miss(key)
            return None
        payload = raw.get("payload")
        fetched_at = raw.get("fetched_at")
        if not fetched_at:
            try:
                fetched_at = date.fromtimestamp(path.stat().st_mtime).isoformat()
            except Exception:  # noqa: BLE001
                fetched_at = None
        self._store[key] = payload
        if fetched_at:
            self._fetched_at[key] = fetched_at
        self.disk_hits += 1
        return {"payload": payload, "fetched_at": fetched_at}

    def get(self, domain: str | None) -> Any | None:
        """The payload for *domain*, or None on a miss."""
        entry = self.get_entry(domain)
        return entry["payload"] if entry else None

    def fetched_at(self, domain: str | None) -> str | None:
        """The ISO date the entry for *domain* was gathered, or None.

        Reads the already-loaded entry when there is one, so asking for the
        date after asking for the payload does not count a second hit.
        """
        key = self._key(domain)
        if key and key in self._store:
            return self._fetched_at.get(key)
        entry = self.get_entry(domain)
        return entry["fetched_at"] if entry else None

    def _note_frozen_miss(self, key: str) -> None:
        if not self.replay_only:
            return
        self.frozen_misses += 1
        line = {
            "record_id": current_record_id.get(),
            "step": FROZEN_MISS,
            "namespace": self.namespace,
            "key": key,
        }
        logger.info(line)
        trace_logger.info(json.dumps(line, default=str))

    # -- writes --------------------------------------------------------------

    def set(self, domain: str | None, payload: Any) -> None:
        """Record *payload* for *domain*. A no-op when the key already exists.

        Immutability is the point: the entry names the day its evidence was
        gathered, and a re-run that overwrote it would move that day forward
        and change the provenance string of a record nothing else about had
        changed.
        """
        key = self._key(domain)
        if not key:
            return
        # Disk first: a fresh in-memory cache in a second run knows nothing
        # about what the first run recorded, and letting it overwrite would
        # move `fetched_at` forward on evidence that was never re-gathered.
        if self._dir is not None and self._path(key).is_file():
            self.get_entry(key)
            return
        if key not in self._store:
            self._store[key] = payload
            self._fetched_at.setdefault(key, _today())
        if self._dir is None:
            return
        path = self._path(key)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "domain": key,
                        "fetched_at": self._fetched_at.get(key, _today()),
                        "payload": payload,
                    },
                    indent=1,
                ),
                encoding="utf-8",
            )
            self.recorded += 1
        except Exception:  # noqa: BLE001 — a fixture we cannot write is not fatal
            pass

    @property
    def stats(self) -> dict[str, int]:
        return {
            f"{self.namespace}_entries": len(self._store),
            f"{self.namespace}_memory_hits": self.memory_hits,
            f"{self.namespace}_disk_hits": self.disk_hits,
            f"{self.namespace}_recorded": self.recorded,
            f"{self.namespace}_frozen_misses": self.frozen_misses,
        }


#: The name this class shipped under, and the one every existing caller and
#: fixture-directory setting uses. It was only ever "page"-specific in its
#: filename prefix; the store itself has always been generic.
PageCache = DiskCache


# ---------------------------------------------------------------------------
# The store registry — one directory, one frozen switch
# ---------------------------------------------------------------------------

class EvidenceCache:
    """Every namespace's :class:`DiskCache`, under one root and one switch.

    Built once per :class:`~enrichment.orchestrator.Orchestrator`. The page and
    Wikidata lanes take their own store from here rather than constructing one,
    so ``CACHE_FROZEN`` reaches all of them at once and the batch summary can
    report a single frozen-miss count across every source.
    """

    #: Namespace → subdirectory of the root. The page and Wikidata directories
    #: keep the names their existing recorded fixtures already live under.
    LAYOUT: dict[str, str] = {
        "page": "page_reads",
        "wikidata": "wikidata",
        "serp": "serp",
        "fetch": "fetch",
        "llm": "llm",
        "ror": "registry",
        "gleif": "registry",
    }

    def __init__(self, root: str | Path | None, *, frozen: bool = False) -> None:
        self.root = Path(root) if root else None
        self.frozen = frozen
        self._stores: dict[str, DiskCache] = {}
        #: Answers this run had to go and get. Incremented by
        #: :func:`note_network_call` at every point the pipeline reaches a
        #: source rather than a recording, whichever namespace it belongs to.
        #: The reproducibility gate asserts this is ZERO on a warm second run.
        self.network_calls = 0
        #: Per-namespace breakdown of the same count, for the findings report.
        self.network_calls_by_namespace: dict[str, int] = {}

    def namespace(
        self,
        name: str,
        *,
        directory: str | Path | None = None,
        replay_only: bool | None = None,
    ) -> DiskCache:
        """The store for *name*, created on first use.

        *directory* overrides the layout (the page and Wikidata lanes have
        their own long-standing settings). ``None`` means "use the layout";
        an EMPTY string means "memory only" — the two are different answers
        and collapsing them is how a lane a caller asked to keep off disk ends
        up writing to it. *replay_only* ORs with the global frozen switch — a
        lane can be frozen on its own, but nothing can un-freeze a frozen run.
        """
        if name in self._stores:
            return self._stores[name]
        if directory is not None:
            path: Path | None = Path(directory) if str(directory).strip() else None
        elif self.root is not None:
            path = self.root / self.LAYOUT.get(name, name)
        else:
            path = None
        store = DiskCache(
            path,
            replay_only=self.frozen or bool(replay_only),
            prefix=name,
            namespace=name,
        )
        self._stores[name] = store
        return store

    @property
    def frozen_misses(self) -> int:
        return sum(s.frozen_misses for s in self._stores.values())

    @property
    def hits(self) -> int:
        return sum(s.memory_hits + s.disk_hits for s in self._stores.values())

    def note_network_call(self, namespace: str) -> None:
        self.network_calls += 1
        self.network_calls_by_namespace[namespace] = (
            self.network_calls_by_namespace.get(namespace, 0) + 1
        )

    @property
    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for store in self._stores.values():
            out.update(store.stats)
        out["evidence_frozen_misses"] = self.frozen_misses
        out["evidence_network_calls"] = self.network_calls
        out["evidence_cache_hits"] = self.hits
        return out


#: The process-wide store. ``tier1_ror`` and ``tier1_lei`` are module-level
#: functions with module-level caches (they predate dependency injection here),
#: so they reach the disk layer through this rather than through eight new
#: parameters. Set by the Orchestrator; None means "memory only", which is what
#: a unit test that never configures one gets.
_active: EvidenceCache | None = None


def set_active_evidence_cache(cache: EvidenceCache | None) -> None:
    """Install *cache* as the process-wide store."""
    global _active
    _active = cache


def active_evidence_cache() -> EvidenceCache | None:
    """The process-wide store, or None when nothing configured one."""
    return _active


def registry_store(registry: str) -> DiskCache | None:
    """The disk store for ``"ror"`` / ``"gleif"``, or None when unconfigured."""
    if _active is None:
        return None
    return _active.namespace(registry)


def note_network_call(namespace: str) -> None:
    """Record that the pipeline went to *namespace*'s source rather than to a
    recording. Called at every point that issues a real request.

    A no-op when no store is configured, which is every unit test — the count
    only means something for a run that has a cache to miss.
    """
    if _active is not None:
        _active.note_network_call(namespace)


class RegistryUnavailableFrozen(RuntimeError):
    """A frozen run wanted a registry response it has no recording of."""


async def cached_registry_get(
    registry: str,
    url: str,
    params: "dict[str, str] | None",
    fetch: Any,
) -> Any:
    """One registry HTTP GET, through the evidence cache. Returns the JSON body.

    **Responses, not decisions.** An earlier version of this recorded the
    *result dict* the client had already computed — which cached the code's
    conclusion rather than the registry's answer, and meant a change to the
    selection rules had no effect on a warm cache. That is the opposite of
    what an evaluation freeze is for: freezing ``dedup/weights.json`` fixes
    the *evidence* so a change to the *logic* can be measured against it. So
    the raw body is what is recorded, keyed on the URL and its query
    parameters, and every guard, every score and every tiebreak is re-decided
    on every run.

    *fetch* is an awaitable taking no arguments; it is called only on a miss.
    Under a frozen store a miss raises :class:`RegistryUnavailableFrozen`,
    which the clients turn into their existing clean-miss result.
    """
    store = registry_store(registry)
    key = http_disk_key(url, params)
    if store is not None:
        recorded = store.get(key)
        if recorded is not None:
            return recorded.get("body") if isinstance(recorded, dict) else recorded
        if store.replay_only:
            raise RegistryUnavailableFrozen(key)
    note_network_call(registry)
    body = await fetch()
    if store is not None:
        store.set(key, {"url": url, "params": params or {}, "body": body})
    return body


def build_evidence_cache(settings: Any) -> EvidenceCache:
    """Construct the store described by *settings* and make it active."""
    root = (getattr(settings, "evidence_cache_dir", "") or "").strip() or None
    cache = EvidenceCache(root, frozen=bool(getattr(settings, "cache_frozen", False)))
    set_active_evidence_cache(cache)
    return cache


# ---------------------------------------------------------------------------
# SERP
# ---------------------------------------------------------------------------

def _serp_to_json(results: Any) -> Any:
    """SERP results → JSON. ``SearchResult`` is a plain dataclass."""
    return [
        {"title": r.title, "url": r.url, "snippet": r.snippet}
        if not isinstance(r, dict) else r
        for r in (results or ())
    ]


def _serp_from_json(raw: Any) -> Any:
    from search.base import SearchResult

    return [
        SearchResult(
            title=d.get("title") or "",
            url=d.get("url") or "",
            snippet=d.get("snippet") or "",
        )
        for d in (raw or ())
        if isinstance(d, dict)
    ]


class SerpCache:
    """Process-level SERP cache, shared across batches and across runs.

    In memory for the life of the orchestrator, and — since Fix B — on disk
    behind that. The in-memory half was the whole cache before, which is why a
    second run of a batch re-issued every search it had already paid for and
    could be served a different result set for the identical query.
    """

    def __init__(self, disk: DiskCache | None = None) -> None:
        self._store: dict[tuple[str, bool, str | None], Any] = {}
        self._disk = disk

    def get(self, query: str, country: str | None = None) -> Any | None:
        key = serp_key(query, country)
        if key in self._store:
            return self._store[key]
        if self._disk is None:
            return None
        raw = self._disk.get(serp_disk_key(query, country))
        if raw is None:
            return None
        results = _serp_from_json(raw)
        self._store[key] = results
        return results

    def set(self, query: str, result: Any, country: str | None = None) -> None:
        self._store[serp_key(query, country)] = result
        if self._disk is not None:
            self._disk.set(serp_disk_key(query, country), _serp_to_json(result))

    @property
    def replay_only(self) -> bool:
        return bool(self._disk is not None and self._disk.replay_only)

    @property
    def size(self) -> int:
        return len(self._store)


def _serp_geo_enabled() -> bool:
    """Whether the record's country is sent to the search provider.

    Read per call rather than captured at import: the setting is a kill switch,
    and a switch that only takes effect on a restart is not one. Failing open
    (True) on any settings problem keeps a search issuing rather than silently
    reverting to the un-localised behaviour this exists to replace.
    """
    try:
        from config import get_settings  # local — utils must not import config
        return bool(get_settings().serp_country_localisation_enabled)
    except Exception:  # noqa: BLE001
        return True


async def cached_serp(
    cache: "BatchCache | None",
    search_client: Any,
    query: str,
    *,
    num_results: int = 5,
    country: str | None = None,
) -> Any:
    """One SERP search, through the cache. THE only way a search is issued.

    Two lanes used to call ``search_client.search`` directly — the person
    affiliation lookup and the lab resolver — so their results were never
    recorded and a second run of a batch re-issued them against a search index
    that had moved on. Routing every lane through here is what makes "zero
    network calls on a warm second run" a property of the pipeline rather than
    of five call sites that each remembered.

    Under :data:`CACHE_FROZEN` a miss returns ``[]`` and issues no call. The
    miss has already been traced as ``evidence-unavailable-frozen`` by the
    store, so the record proceeds with no SERP evidence for this query rather
    than with fresh evidence the frozen run was supposed to exclude.

    A :class:`~search.base.SearchUnavailable` — the provider could not execute
    the search at all — returns ``[]`` and records **nothing**. Any other
    exception propagates: each caller has its own idea of what a failed lane
    means, and swallowing them here would flatten that.

    *country* now reaches the provider as well as the cache key. It used to do
    only the latter, which meant two records in different countries were filed
    under different keys for a search that had been issued identically — the
    key promised a distinction the request never made. Normalisation to an ISO
    alpha-2 code happens HERE rather than in each client, so every provider
    receives the same well-formed value or nothing, and the RAW string stays
    the cache key so existing entries keep resolving.
    """
    if cache is not None:
        hit = cache.get_serp(query, country)
        if hit is not None:
            return hit
        if cache.serp_frozen:
            return []
    note_network_call("serp")
    try:
        results = await search_client.search(
            query,
            num_results=num_results,
            country=country_to_iso_code(country) if _serp_geo_enabled() else None,
        )
    except SearchUnavailable:
        # The search could not be executed. NOT recorded: a dropped connection
        # is not evidence that the organisation has no web presence, and this
        # cache is durable — one bad afternoon would otherwise become a
        # permanent empty result. Callers have always treated a failed search
        # as no candidates, and still do.
        return []
    if cache is not None:
        cache.set_serp(query, results, country)
    return results


class BatchCache:
    """Dict-based cache scoped to a single enrichment batch.

    When constructed with a ``shared_serp`` store, SERP lookups fall
    through to it on a per-batch miss and writes propagate to it, so
    results are reused across batches within the same process — and, since
    Fix B, across runs of the process too.
    """

    def __init__(self, shared_serp: SerpCache | None = None) -> None:
        self._serp: dict[tuple[str, bool, str | None], Any] = {}
        self._shared_serp = shared_serp
        # Legacy (lowercase-only) SERP keys that have actually been queried.
        # A normalised hit whose legacy key was never queried is a lookup the
        # old key would have missed — that count is the
        # `cache_hits_after_normalisation` telemetry, nothing more.
        self._serp_legacy_seen: set[tuple[str, bool, str | None]] = set()
        self._serp_normalised_hits = 0
        # Per-batch cache of a resolved institution host (redirect-followed /
        # subdomain-aware) so the department probe costs one resolution per
        # institution, not one per stage.
        self._resolved_host: dict[str, str] = {}

    # -- Resolved institution host (department probe base) --------------------

    def get_resolved_host(self, key: str) -> str | None:
        return self._resolved_host.get((key or "").strip().lower())

    def set_resolved_host(self, key: str, value: str) -> None:
        self._resolved_host[(key or "").strip().lower()] = value

    # -- SERP ----------------------------------------------------------------
    #
    # There is no ROR namespace here. `get_ror`/`set_ror` existed but had no
    # callers in the whole codebase — ROR lookups have always consulted the
    # module-level `_ror_cache` in enrichment/tier1_ror.py, which is the cache
    # that Step 1's normalisation had to reach. The dead pair is gone rather
    # than left to imply a layer that never ran.

    @property
    def serp_frozen(self) -> bool:
        """True when a SERP miss must NOT go to the network (CACHE_FROZEN)."""
        return bool(self._shared_serp is not None and self._shared_serp.replay_only)

    def get_serp(self, query: str, country: str | None = None) -> Any | None:
        """Retrieve a cached SERP result by normalised query + country.

        Checks the per-batch cache first, then the shared process-level
        cache (promoting a shared hit into the batch cache).
        """
        key = serp_key(query, country)
        legacy = legacy_serp_key(query, country)
        hit = None
        if key in self._serp:
            hit = self._serp[key]
        elif self._shared_serp is not None:
            data = self._shared_serp.get(query, country)
            if data is not None:
                self._serp[key] = data
                hit = data
        if hit is not None and legacy not in self._serp_legacy_seen:
            # Served under the normalised key; the lowercased key would have
            # missed and paid for another SERP call.
            self._serp_normalised_hits += 1
        return hit

    def set_serp(self, query: str, result: Any, country: str | None = None) -> None:
        """Cache a SERP result in the batch cache and the shared cache."""
        self._serp[serp_key(query, country)] = result
        self._serp_legacy_seen.add(legacy_serp_key(query, country))
        if self._shared_serp is not None:
            self._shared_serp.set(query, result, country)

    # -- Diagnostics ---------------------------------------------------------

    @property
    def normalised_hits(self) -> int:
        """SERP lookups the normalised key served that the old lowercased key
        would have missed."""
        return self._serp_normalised_hits

    @property
    def stats(self) -> dict[str, int]:
        return {
            "serp_entries": len(self._serp),
            "serp_normalised_hits": self._serp_normalised_hits,
        }
