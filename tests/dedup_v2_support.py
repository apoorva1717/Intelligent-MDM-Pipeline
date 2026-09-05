"""Shared scaffolding for the dedup v2 fixture tests (not a test module).

Holds three things the two ``test_dedup_v2*`` modules share: the fixture
loader, the expectation tables transcribed from the change request, and the
three LLM doubles that stand in for the adjudicator model.

Why doubles at all
------------------

Phase 2 has no cache and no replay layer (docs/13_CLUSTERING_DOSSIER.md §6.1),
so a test that called the real deployment would be neither offline nor
repeatable. Each double therefore answers a *stated* policy, and each test says
which policy it is asserting under:

``SpecOracleLLM``
    A perfect adjudicator: it answers exactly what the expectation tables below
    say, falling back to the workbook's own ``gt_dup_group`` for every pair the
    tables do not mention. Under this double a failure means the deterministic
    machinery (blocking, slot classification, buckets, guards, emission) either
    *forbade* a correct merge or *forced* an incorrect one — never that the
    model was wrong. That is the property the v2 flags are supposed to fix.

``AlwaysSameEntityLLM``
    A maximally wrong adjudicator: everything it is asked about is the same
    entity. Under this double a MUST_MERGE group that fails to cluster proves
    its rows never reached one block or were never nominated — a pure blocking
    result, independent of any model judgement. It is also the only way to
    assert that a separation is *structural* rather than model-dependent.

``V1ReplayLLM``
    Replays the clustering the recorded v1 run actually produced, reconstructed
    from the ``Cluster ID`` / ``Routing`` columns the workbook carries. It is
    what makes the flags-off test possible: hold the verdicts fixed at v1's and
    any change in the output columns is this repository's doing.

None of the three tells you what the live model would answer. That claim needs
a live run, and the report says so rather than the test pretending otherwise.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from dedup.llm import DedupLLMResult
from dedup.models import DedupRow
from dedup.signatures import normalize_key

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "dedup_v2_stress_200.json"

#: The three v2 feature flags, all default-false. Every one of them gates one
#: change; the flags-off path must stay byte-identical to v1.
V2_FLAGS = ("DEDUP_V2_BLOCKING", "DEDUP_V2_NAME2", "DEDUP_V2_ID_CONFLICT")


# ---------------------------------------------------------------------------
# Expectation tables (transcribed from the change request)
# ---------------------------------------------------------------------------

#: Groups whose rows must end up sharing one ``Cluster ID``. Compared as set
#: membership — cluster ids are content hashes, so the literal value is not
#: assertable, only who is inside.
MUST_MERGE: dict[str, list[str]] = {
    "Lee":                  ["13216611", "13340941"],
    "Stanford":             ["13348869", "13359026"],
    "Takeda40":             ["13017986", "13057204"],
    "UTSA":                 ["13044882", "13044976"],
    "CWRU2109":             ["13210816", "13337284"],
    "CWRU2080":             ["13130623", "13141440"],
    "CWRU2210":             ["13337285", "13349043"],
    "USC_Norris":           ["13134277", "13213881"],
    "Shell":                ["13119312", "13222790"],
    "Army_ACC":             ["13048062", "13146786"],
    "VA_Dallas":            ["13336698", "13346536", "13348400"],
    "JFK":                  ["13334236", "13335826", "13344636"],
    "Marian":               ["13016967", "13333500", "13335926", "13343787"],
    "StElizabeth":          ["13336040", "13336285", "13343790"],
    "Methodist":            ["13145924", "13335060", "13344194"],
    "MedicalCity":          ["13145693", "13344098"],
    "Hoag":                 ["13334046", "13335012", "13336374"],
    "HGST_GreatOaks":       ["13057667", "13118081"],
    "HGST_Yerba":           ["13038460", "13192407"],
    "RRDS":                 ["13079821", "13181372"],
    "USG":                  ["13104512", "13158570"],
    "GES_Hellyer":          ["13017251", "13226604"],
    "GES_Qume":             ["13223469", "13234427"],
    "EMD_RDI":              ["13135468", "13138597", "13353599", "13364185"],
    "UCSF_Folsom":          ["13161437", "13342545"],
    "Nova":                 ["13130303", "13351065"],
    "PAVIR_Miranda":        ["13345935", "13346170"],
    "Scripps_Waples":       ["13017979", "13336447"],
    "UTRGV":                ["13144897", "13223387"],
    "Merck_Cambridge_bare": ["13118369", "13359185"],
    "Labcorp":              ["13348403", "13364234"],
    "Covia":                ["13113215", "13128534"],
    "Merck_Rahway":         ["13189884", "13334413", "13347414"],
    "AssayDepot":           ["13035402", "13364744"],
    "Bruker":               ["13238351", "IC1280"],
}

@dataclass(frozen=True)
class LinkGroup:
    """One organisation, more than one site.

    ``rows`` all name the same organisation and must therefore share a
    ``Link ID``. ``sites`` partitions them by delivery point: no two sites may
    share a ``Cluster ID``, because a Link says "same organisation" and a
    Cluster says "same record". Keeping the two claims in separate columns is
    the whole point of the Link ID — it is the only place a same-organisation
    finding can live without asserting a duplicate.

    Membership within one site is left to the model and is not asserted here.
    """

    rows: tuple[str, ...]
    sites: tuple[tuple[str, ...], ...]
    why: str
    #: What a correct adjudicator says about the INSTITUTIONS — "same" for one
    #: organisation written more than one way, "different" where the model has
    #: standing to disagree with the registry (a parent and its institute
    #: sharing a ROR). Drives the oracle's ``institution_relation``.
    relation: str = "same"
    #: Whether the link must also route to review. ``True`` = must, ``False`` =
    #: must not, ``None`` = this group's routing is decided by something else
    #: (an unverifiable address, an id conflict) and is asserted elsewhere.
    #: A link is normally just a statement of relationship; it becomes a
    #: steward's question only when the evidence and the model disagree.
    review: Optional[bool] = None


#: Same organisation, different delivery points. Every one of these is a real
#: link the fixture carries, and each is reached by a different route: a shared
#: ROR across blocks, a shared LEI, a row carrying both identifiers that
#: bridges two sites, and a row with no identifier at all that reaches the link
#: only through an in-block merge.
MUST_LINK: dict[str, LinkGroup] = {
    "NASA_Ames": LinkGroup(
        rows=("13036862", "13128613", "13057138", "13120409"),
        sites=(("13036862",), ("13128613", "13057138", "13120409")),
        why=(
            "All four carry ROR 027ka1x80. Only 13036862 names a house number "
            "(239 Mark Ave); the other three name no delivery point, so they "
            "may never share its Cluster ID. This is the pair the change "
            "request first wrote as a MERGE — it is a Link."
        ),
    ),
    "HGST": LinkGroup(
        rows=("13057667", "13118081", "13038460", "13192407"),
        sites=(("13057667", "13118081"), ("13038460", "13192407")),
        why=(
            "Great Oaks Pkwy and Yerba Buena Rd. The two 'Hitachi Global "
            "Storage Technologies' rows share ROR 02q0s1x22 across the blocks; "
            "the two 'HGST Inc' rows carry no identifier and reach the link "
            "only through the merge inside their own block."
        ),
    ),
    "Scripps": LinkGroup(
        rows=("13017979", "13336447", "13335883", "13336451"),
        sites=(("13017979", "13336447"), ("13335883", "13336451")),
        why=(
            "9365 Waples St and 9060 Activity Rd. Three rows share ROR "
            "02dxx6824; 13335883 carries a different one (04v7hvq31) and "
            "reaches the link through the merge in its own block — which is "
            "also why that block routes to review."
        ),
    ),
    "Merck": LinkGroup(
        rows=("13118369", "13359185", "13189884", "13334413", "13347414"),
        sites=(("13118369", "13359185"), ("13189884", "13334413", "13347414")),
        why=(
            "320 Bent St, Cambridge and 126 E Lincoln Ave, Rahway. The "
            "Cambridge rows carry LEI 4YV9Y5M8S0BRK1RP0397 and the Rahway rows "
            "ROR 02891sr49; 13334413 carries both and is the only thing "
            "bridging the two sites."
        ),
    ),
    "Stanford_family": LinkGroup(
        rows=("13348869", "13367825"),
        sites=(("13348869",), ("13367825",)),
        review=False,
        why=(
            "One institution at 1291 Welch Rd, two entities: a bare Stanford "
            "University record and Stanford's Fairchild Science department. "
            "Same organisation, so they share a Link ID; different entities, "
            "so they must not share a Cluster ID. Nothing is in doubt here — "
            "no review."
        ),
    ),
    "Army_family": LinkGroup(
        rows=("13048062", "13146532"),
        sites=(("13048062",), ("13146532",)),
        review=False,
        why=(
            "United States Army at 6501 E 11 Mile Rd: Army Contracting Command "
            "and Devcom Ground Vehicle Systems Center. One institution, two "
            "commands."
        ),
    ),
    "Merck_MRL": LinkGroup(
        rows=("13118369", "13348301"),
        sites=(("13118369",), ("13348301",)),
        review=False,
        why=(
            "Merck & Co., Inc. at 320 Bent St, bare and with Merck Research "
            "Laboratories below it. One institution, two entities."
        ),
    ),
    "EMD_family": LinkGroup(
        rows=("13033988", "13364185"),
        sites=(("13033988",), ("13364185",)),
        why=(
            "EMD Serono, Inc. and EMD Serono Research and Development "
            "Institute, Inc. share ROR 027zrs220 and are two legal entities. "
            "The registry says one organisation and the model says two — a "
            "disagreement between sources that both have standing, which is "
            "exactly a steward's question. Linked AND routed to review."
        ),
        relation="different",
        review=True,
    ),
    "PAVIR": LinkGroup(
        rows=("13345790", "13345937", "13345935", "13346170"),
        sites=(("13345790", "13345937"), ("13345935", "13346170")),
        why=(
            "All four carry ROR 008e03r59. Two name 3801 Miranda Ave; the "
            "other two name no usable address at all and cluster only with "
            "each other, in a block routed to review."
        ),
    ),
}

@dataclass(frozen=True)
class ReviewLink:
    """Related, routed to a human, and NOT asserted as a duplicate.

    Two shapes, and the difference is which id carries the finding:

    ``shares_cluster=True``
        One entity, but nothing verified it — an address that cannot be checked
        (PAVIR), or two ROR ids that disagree (Scripps). The Cluster ID stands
        and the routing says a person must confirm it.
    ``shares_cluster=False``
        Not one entity, and not unrelated either. The evidence says one
        organisation; a real corporate distinction says not one record. The
        finding lives in the Link ID, and there is deliberately no Cluster ID
        to overstate it.
    """

    rows: tuple[str, ...]
    shares_cluster: bool
    why: str
    relation: str = "same"


#: Routed to review, every member, in all three cases.
MUST_LINK_FOR_REVIEW: dict[str, ReviewLink] = {
    "PAVIR_noaddr": ReviewLink(
        rows=("13345790", "13345937"),
        shares_cluster=True,
        why="One organisation, one entity — at a delivery point neither row names.",
    ),
    "Scripps_Activity": ReviewLink(
        rows=("13335883", "13336451"),
        shares_cluster=True,
        why=(
            "One entity at 9060 Activity Rd carrying two different ROR ids. "
            "The conflict is the finding; exploding the entity into singletons "
            "loses it. Change D."
        ),
    ),
    "UTSW": ReviewLink(
        rows=("13185655", "13350355"),
        shares_cluster=False,
        why=(
            "Utwmc LLC vs UT Southwestern Medical Center — acronym evidence, "
            "different legal-entity form; steward decision by design. Moved "
            "here from MUST_MERGE: the model is right that an LLC is a real "
            "corporate distinction, and the expectation was wrong to demand a "
            "merge it cannot honestly make."
        ),
        relation="different",
    ),
}

#: Merge groups whose rows name no delivery point. The merge stands — the names
#: say these are one record — but the cluster routes to review because nothing
#: established the address behind it. Asserted so the rule stays visible: it is
#: the only thing standing between "the names matched" and "these are the same
#: customer".
ADDRESS_LESS_MERGE_GROUPS = ("Lee", "USG")

#: Expected to keep failing until Phase 1 stops overflowing "…and Technology"
#: out of Name 1 and into Street 1. Marked xfail; not chased here.
XFAIL: dict[str, list[str]] = {
    "NIST": ["13338550", "13136808"],
}

#: Rows that must never share a ``Cluster ID`` with the named anchor group.
#:
#: Transcribed as a list of triples rather than the request's dict literal on
#: purpose: that literal repeats the key ``UCLA_bare(13342488)`` twice, and a
#: Python dict would silently drop the first entry (``13341685``, the
#: bare-institution-vs-department trap) and keep only the second. Both are
#: asserted here.
#:
#: ``anchors`` is the group the forbidden rows must stay out of; when it is a
#: MUST_MERGE key the whole group is the anchor.
MUST_NOT_MERGE: list[tuple[str, list[str], list[str]]] = [
    # -- bare institution vs a real department at the same delivery point ----
    ("Stanford / Fairchild Science",
     MUST_MERGE["Stanford"], ["13367825"]),
    ("UCLA bare vs Center for Systems Biomedicine",
     ["13342488"], ["13341685"]),
    ("Merck Cambridge bare vs MRL / ESC",
     MUST_MERGE["Merck_Cambridge_bare"], ["13348301", "13364371"]),
    ("Takeda 35 vs TDC Americas",
     ["13341783"], ["13057338"]),
    ("EMD RDI vs EMD Serono, Inc. (bare)",
     MUST_MERGE["EMD_RDI"], ["13033988"]),
    # -- a different department at the same delivery point -------------------
    ("Army ACC vs Devcom GVSC",
     MUST_MERGE["Army_ACC"], ["13146532", "13213081"]),
    ("Merck Rahway vs MRL - Kenilworth",
     MUST_MERGE["Merck_Rahway"], ["13348052"]),
    # -- a different institution at the same delivery point ------------------
    ("Takeda 40 vs Takeda Oncology",
     MUST_MERGE["Takeda40"], ["13038528"]),
    ("UTSA vs UT Health / Texas A&M / THSU",
     MUST_MERGE["UTSA"], ["13129468", "13131049", "13046339"]),
    ("HP Inc vs Hewlett Packard Enterprise",
     ["13039054"], ["13057984"]),
    # -- a different house number on the same street / zip -------------------
    ("UCLA 675 vs 695",
     ["13342488"], ["13349159"]),
    ("Bruker 40 Manning vs 15 Fortune",
     MUST_MERGE["Bruker"], ["13016575"]),
    ("UCSF 1855 Folsom vs 1550 4th",
     MUST_MERGE["UCSF_Folsom"], ["13334454"]),
    ("UTRGV 1201 W University vs 1407 E Freddy Gonzalez",
     MUST_MERGE["UTRGV"], ["13044748"]),
    ("Labcorp 655 Fairfield vs 800 Technology",
     MUST_MERGE["Labcorp"], ["13346510"]),
    ("Assay Depot 505 Lomas Santa Fe vs N Acacia / S Hwy 101",
     MUST_MERGE["AssayDepot"], ["13346804", "13366953"]),
    # No address: may LINK, must never MERGE into the one row that names a door.
    ("NASA Mark Ave vs the address-less ISD rows",
     ["13036862"], ["13057138", "13120409", "13128613"]),
]

#: Outcomes that depend on the model's judgement, not on any deterministic
#: rule. Reported in the change report, never asserted — a build that went red
#: here would be reporting the model's opinion as a defect, and a build that
#: went green would be claiming a guarantee that does not exist.
#:
#: Each entry is (label, rows, what to look for).
MODEL_JUDGEMENT: list[tuple[str, list[str], str]] = [
    (
        "NASA: 13128613 vs the two address-less ISD rows",
        ["13128613", "13057138", "13120409"],
        "All three name no delivery point, so they share one block and only "
        "the model separates 'Ames Research Center' from 'Intelligent Systems "
        "Division / Ames Research Center'. Blocking guarantees nothing here.",
    ),
]

#: The subset of MUST_NOT_MERGE that delivery-point blocking alone must
#: enforce — a different house number, or no comparable address at all. These
#: must hold even when the model is maximally wrong, because no model verdict
#: is consulted across an incompatible delivery point.
#:
STRUCTURAL_MUST_NOT_MERGE = frozenset({
    "UCLA 675 vs 695",
    "Bruker 40 Manning vs 15 Fortune",
    "UCSF 1855 Folsom vs 1550 4th",
    "UTRGV 1201 W University vs 1407 E Freddy Gonzalez",
    "Labcorp 655 Fairfield vs 800 Technology",
    "Assay Depot 505 Lomas Santa Fe vs N Acacia / S Hwy 101",
    "NASA Mark Ave vs the address-less ISD rows",
})



def forbidden_pairs() -> set[frozenset[str]]:
    """Every unordered row pair MUST_NOT_MERGE forbids."""
    pairs: set[frozenset[str]] = set()
    for _label, anchors, forbidden in MUST_NOT_MERGE:
        for a in anchors:
            for b in forbidden:
                if a != b:
                    pairs.add(frozenset({a, b}))
        # "must not merge with each other" — the forbidden rows are also
        # forbidden among themselves (HP/HPE, the two Assay Depot sites).
        for i, b1 in enumerate(forbidden):
            for b2 in forbidden[i + 1:]:
                pairs.add(frozenset({b1, b2}))
    return pairs


def expected_group_of(row_id: str) -> Optional[str]:
    """The expectation-table group a row belongs to, if the tables name one."""
    for table in (MUST_MERGE, XFAIL):
        for name, ids in table.items():
            if row_id in ids:
                return name
    for name, link in MUST_LINK_FOR_REVIEW.items():
        if row_id in link.rows:
            return name
    return None


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

def load_fixture() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def fixture_row_dicts(fixture: dict[str, Any]) -> list[dict[str, str]]:
    """The fixture's rows in sheet order — what ``_parse_xlsx`` would hand on."""
    return [fixture["rows"][row_id] for row_id in fixture["order"]]


def fixture_dedup_rows(fixture: dict[str, Any]) -> list[DedupRow]:
    """Bind the fixture through the real XLSX→DedupRow path.

    Deliberately the production binder (``api.routes._rows_to_dedup_rows``) and
    not a hand-built ``DedupRow`` list: the alias table is itself part of what
    the change touches (C.4 binds six columns that are dropped today), so a
    test that bypassed it could pass while the file route still dropped them.
    """
    from api.routes import _rows_to_dedup_rows

    return _rows_to_dedup_rows(fixture_row_dicts(fixture))


# ---------------------------------------------------------------------------
# Resolving a prompt payload back to fixture rows
# ---------------------------------------------------------------------------

_DEPT_COLUMNS = ("Name 2", "Name 3", "Name 4", "Name 5")


def _dept_text(row: dict[str, str], *, skip_name2: bool = False) -> str:
    columns = _DEPT_COLUMNS[1:] if skip_name2 else _DEPT_COLUMNS
    return " / ".join(p for p in (row.get(c, "").strip() for c in columns) if p)


def _exact_form(row: dict[str, str]) -> tuple[str, str]:
    """The v1 signature key: Name 1 against the whole block below it."""
    return normalize_key(row.get("Name 1", "").strip()), normalize_key(_dept_text(row))


def _alternative_forms(row: dict[str, str]) -> set[tuple[str, str]]:
    """The other (institution, department) shapes v2 can present this row as.

    v1 only ever shows ``(Name 1, Name2..Name5)``. v2's slot classifier can
    also show the row as institution-only (logistics / alias / contact text is
    not a department), as ``Name 1 + Name 2`` (overflow), or as ``Name 2``
    alone (Name 1 was an opaque code). Indexing those keeps the doubles able
    to identify a signature under either flag setting.

    They are kept SEPARATE from the exact form and consulted only after it
    misses. Folding them together makes the index over-broad in a way that is
    silently wrong: ``EMD Serono, Inc.`` bare and ``EMD Serono, Inc.`` +
    ``Research and Development Institute`` are two rows at one address, and an
    index that let the second answer to the first's key merged them.
    """
    name1 = row.get("Name 1", "").strip()
    name2 = row.get("Name 2", "").strip()
    rest = _dept_text(row, skip_name2=True)
    forms = {
        (normalize_key(name1), normalize_key(rest)),
        (normalize_key(name1), ""),
    }
    if name2:
        forms.add((normalize_key(f"{name1} {name2}"), ""))
        forms.add((normalize_key(f"{name1} {name2}"), normalize_key(rest)))
        forms.add((normalize_key(name2), ""))
    return forms - {_exact_form(row)}


class SignatureIndex:
    """Maps a prompt payload entry back to the fixture rows behind it.

    Two rows in different blocks routinely carry the same name — ``HGST Inc``
    sits at both Great Oaks Pkwy and Yerba Buena Rd, ``Scripps Research
    Institute`` at both Activity Rd and Waples St. Name alone therefore cannot
    identify a signature, so resolution takes the block into account: every
    signature in one prompt belongs to one block, and ``resolve_all`` picks the
    block that explains the most of them before narrowing each entry to it.
    """

    def __init__(self, fixture: dict[str, Any]) -> None:
        self._exact: dict[tuple[str, str], set[str]] = {}
        self._alt: dict[tuple[str, str], set[str]] = {}
        self._by_name1: dict[str, set[str]] = {}
        for row_id, row in fixture["rows"].items():
            self._exact.setdefault(_exact_form(row), set()).add(row_id)
            for form in _alternative_forms(row):
                self._alt.setdefault(form, set()).add(row_id)
            self._by_name1.setdefault(
                normalize_key(row.get("Name 1", "")), set()
            ).add(row_id)
        self._blocks: dict[str, set[str]] = {}

    def bind_blocks(self, blocks: dict[str, set[str]]) -> None:
        """Record which rows share a block, as ``{block_id: {row_id, …}}``."""
        self._blocks = blocks

    def resolve(self, entry: dict[str, Any]) -> set[str]:
        """Row ids behind one payload signature / canonical entity.

        ``name1``/``name2`` are tried before ``institution``/``department``:
        the first pair is always the signature's verbatim strings, while the
        second is a short label the LLM itself wrote on a previous turn
        (dedup/adjudicator.py:631-632) and so may be anything at all.

        Raises rather than guessing: a double that answered about rows it had
        misidentified would produce a green test for the wrong reason.
        """
        attempts = [
            (str(entry.get("name1") or ""), str(entry.get("name2") or "")),
            (str(entry.get("institution") or ""), str(entry.get("department") or "")),
        ]
        for table in (self._exact, self._alt):
            for institution, department in attempts:
                if not institution:
                    continue
                key = (normalize_key(institution), normalize_key(department))
                if key in table:
                    return table[key]
        for institution, _department in attempts:
            if normalize_key(institution) in self._by_name1:
                return self._by_name1[normalize_key(institution)]
        raise LookupError(f"no fixture row matches payload entry {attempts!r}")

    def resolve_all(self, entries: list[dict[str, Any]]) -> list[set[str]]:
        """Resolve every entry of one prompt, narrowed to a single block."""
        raw = [self.resolve(entry) for entry in entries]
        if not self._blocks:
            return raw
        best: Optional[tuple[int, int, str]] = None
        for block_id, members in self._blocks.items():
            explained = sum(1 for rows in raw if rows & members)
            if explained == 0:
                continue
            # Most entries explained wins; then the block that adds the least
            # noise; then the id, so the choice never depends on dict order.
            key = (-explained, len(members), block_id)
            if best is None or key < best:
                best = key
        if best is None:
            return raw
        members = self._blocks[best[2]]
        return [(rows & members) or rows for rows in raw]


# ---------------------------------------------------------------------------
# The LLM doubles
# ---------------------------------------------------------------------------

_MODE_B_MARKER = "Decide whether the candidate"


def _payload(user_prompt: str) -> dict[str, Any]:
    """The JSON payload out of any of the four prompt templates.

    v1 heads its partition listing "Signatures:", v2 heads it "Records:"; both
    pairwise templates end with the payload after a blank line.
    """
    # v5 appends an "evidence:" block after the payload; cut it off before
    # parsing. The doubles answer from the expectation tables, not from the
    # hints, so they read the records and ignore the block — which is also the
    # point of keeping it out of the JSON.
    body = user_prompt.split("\nevidence (", 1)[0]
    for marker in ("Signatures:\n", "Records:\n"):
        if marker in body:
            return json.loads(body.split(marker, 1)[1])
    return json.loads(body.rsplit("\n\n", 1)[1])


class _RecordingLLM:
    """Base double: counts calls and records prompts."""

    model = "dedup-v2-double"

    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture
        self.index = SignatureIndex(fixture)
        self.calls = 0
        self.mode_a_calls = 0
        self.pair_calls = 0
        self.prompts: list[str] = []

    def bind_blocks(self, blocks: dict[str, set[str]]) -> None:
        """Told which rows share a block, so a name can be resolved to a row.

        Called by ``run_clustering`` from the blocking the run itself will use,
        so the double sees v2's blocks when the flag is on and v1's when it is
        off — never a hard-coded idea of what a block is.
        """
        self.index.bind_blocks(blocks)

    def _pair_entries(self, payload: dict[str, Any]) -> tuple[set[str], list[set[str]]]:
        """Resolve a pairwise payload: (candidate rows, one set per entity)."""
        entities = payload.get("entities", [])
        resolved = self.index.resolve_all([payload["candidate"], *entities])
        return resolved[0], resolved[1:]

    async def adjudicate(self, system_prompt: str, user_prompt: str, *, max_tokens: int = 4000):
        self.calls += 1
        self.prompts.append(user_prompt)
        if _MODE_B_MARKER in user_prompt:
            self.pair_calls += 1
            raw = self._answer_pair(_payload(user_prompt))
        else:
            self.mode_a_calls += 1
            raw = self._answer_partition(_payload(user_prompt))
        return DedupLLMResult(
            raw=raw,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0,
            model_version=self.model,
            error=None,
        )

    # -- subclass hooks -----------------------------------------------------
    def _answer_partition(self, payload: dict[str, Any]) -> str:  # pragma: no cover
        raise NotImplementedError

    def _answer_pair(self, payload: dict[str, Any]) -> str:  # pragma: no cover
        raise NotImplementedError


class SpecOracleLLM(_RecordingLLM):
    """Answers exactly what the expectation tables say.

    Precedence, and it matters: an explicit MUST_NOT_MERGE pair is different
    even when the workbook's own ``gt_dup_group`` says otherwise (the UCLA and
    Merck bare-vs-department traps are like this), and an explicit MUST_MERGE
    group is the same entity even where ``gt_expected_action`` says LINK rather
    than MERGE (UTRGV, Covia, NASA — the request's tables supersede the
    workbook there).
    """

    def __init__(self, fixture: dict[str, Any]) -> None:
        super().__init__(fixture)
        self._forbidden = forbidden_pairs()
        self._gt = {
            row_id: row.get("gt_dup_group", "")
            for row_id, row in fixture["rows"].items()
        }

    def _review_only(self, left: Iterable[str], right: Iterable[str]) -> bool:
        """A pair that is one organisation but must not become one record."""
        left, right = set(left), set(right)
        for link in MUST_LINK_FOR_REVIEW.values():
            if link.shares_cluster:
                continue
            if left & set(link.rows) and right & set(link.rows):
                return True
        return False

    def _institution_relation(
        self, left: Iterable[str], right: Iterable[str]
    ) -> Optional[str]:
        """What a correct adjudicator says about these two INSTITUTIONS.

        Separate from ``_same_entity`` on purpose — that is the question the
        Cluster ID answers, and this is the question the Link ID answers. A
        pair can be one institution and two entities (Stanford bare against
        Stanford's Fairchild department), and a pair can be two institutions
        the registry files under one id (EMD Serono against its own research
        institute).

        The tables answer it where they speak. Everywhere else the default is
        the deterministic evidence: ``pair_evidence`` exists precisely to be a
        same-institution signal, so absent a table saying otherwise, agreeing
        with it is what a correct adjudicator does.
        """
        left, right = set(left), set(right)
        for table in (MUST_LINK, MUST_LINK_FOR_REVIEW):
            for spec in table.values():
                members = set(spec.rows)
                if left & members and right & members:
                    return spec.relation
        for a in left:
            for b in right:
                group_a, group_b = expected_group_of(a), expected_group_of(b)
                if group_a is not None and group_a == group_b:
                    return "same"
        return None

    def _same_entity(self, left: Iterable[str], right: Iterable[str]) -> bool:
        left, right = set(left), set(right)
        if self._review_only(left, right):
            return False
        for a in left:
            for b in right:
                if frozenset({a, b}) in self._forbidden:
                    return False
        for a in left:
            for b in right:
                if a == b:
                    return True
                group_a, group_b = expected_group_of(a), expected_group_of(b)
                if group_a is not None and group_a == group_b:
                    return True
                if group_a is None and group_b is None:
                    if self._gt.get(a) and self._gt[a] == self._gt.get(b):
                        return True
        return False

    def _answer_partition(self, payload: dict[str, Any]) -> str:
        signatures = payload.get("signatures", [])
        rows = self.index.resolve_all(signatures)
        resolved = {s["signature_id"]: r for s, r in zip(signatures, rows)}
        groups: list[list[str]] = []
        for sig in signatures:
            sid = sig["signature_id"]
            for group in groups:
                if any(self._same_entity(resolved[sid], resolved[other]) for other in group):
                    group.append(sid)
                    break
            else:
                groups.append([sid])
        # A signature that is review-only against any other in this call is
        # reported uncertain rather than grouped — the v7 instruction.
        review = {
            sid
            for group in groups for sid in group
            for other in groups for osid in other
            if osid != sid and self._review_only(resolved[sid], resolved[osid])
        }
        relations: dict[str, str] = {}
        for sig in signatures:
            sid = sig["signature_id"]
            verdicts = {
                self._institution_relation(resolved[sid], resolved[other])
                for other in resolved if other != sid
            }
            # One field, several counterparts: report the strongest signal, so
            # a disagreement is never hidden behind an agreement elsewhere.
            for candidate in ("different", "uncertain", "same"):
                if candidate in verdicts:
                    relations[sid] = candidate
                    break
            else:
                relations[sid] = "same" if len(resolved) > 1 else "different"
        return json.dumps({
            "institution_relation": relations,
            "entities": [
                {
                    "signature_ids": [s for s in group if s not in review],
                    "institution": "",
                    "department": "",
                    "confidence": 1.0,
                    "reasoning": "SpecOracleLLM: grouped from the expectation tables.",
                }
                for group in groups
                if [s for s in group if s not in review]
            ],
            "uncertain_signature_ids": sorted(review),
            "uncertain_reasons": {
                sid: (
                    "SpecOracleLLM: one organisation, but a different legal "
                    "entity — a steward decision, not a merge."
                )
                for sid in sorted(review)
            },
        })

    def _answer_pair(self, payload: dict[str, Any]) -> str:
        candidate, entity_rows = self._pair_entries(payload)
        relation = next(
            (
                verdict
                for rows in entity_rows
                if (verdict := self._institution_relation(candidate, rows))
            ),
            "same" if entity_rows else "different",
        )
        for entity, rows in zip(payload.get("entities", []), entity_rows):
            if self._same_entity(candidate, rows):
                return json.dumps({
                    "decision": "match",
                    "matched_entity_id": entity["entity_id"],
                    "confidence": 1.0,
                    "reasoning": "SpecOracleLLM: same entity per the expectation tables.",
                    "department_relation": "same",
                    "institution_relation": relation,
                })
        return json.dumps({
            "decision": "new",
            "matched_entity_id": None,
            "confidence": 1.0,
            "reasoning": "SpecOracleLLM: different entity per the expectation tables.",
            "department_relation": "different",
            "institution_relation": relation,
        })


class AlwaysSameEntityLLM(_RecordingLLM):
    """The adversary: every question it is asked is answered "same entity".

    What survives this double is deterministic; what does not, is the model's
    call. Nothing else about it is interesting.
    """

    def _answer_partition(self, payload: dict[str, Any]) -> str:
        return json.dumps({
            "entities": [{
                "signature_ids": [s["signature_id"] for s in payload.get("signatures", [])],
                "institution": "",
                "department": "",
                "confidence": 1.0,
                "reasoning": "AlwaysSameEntityLLM: merged everything on purpose.",
            }],
            "uncertain_signature_ids": [],
        })

    def _answer_pair(self, payload: dict[str, Any]) -> str:
        entities = payload.get("entities", [])
        return json.dumps({
            "decision": "match" if entities else "new",
            "matched_entity_id": entities[0]["entity_id"] if entities else None,
            "confidence": 1.0,
            "reasoning": "AlwaysSameEntityLLM: merged on purpose.",
        })


class V1ReplayLLM(_RecordingLLM):
    """Replays the clustering the recorded v1 run produced.

    Reconstructed from the workbook's own output columns: rows sharing a
    non-empty ``Cluster ID`` were one entity, a blank one was a singleton, and
    ``Routing == manual_review`` means that row's signature came out of the
    bucketed pass uncertain. Feeding those verdicts back in holds the model
    constant, so anything that moves in ``Cluster ID`` / ``Routing`` moved
    because this repository changed.

    It does not reproduce ``Reasoning``: that column is model prose, and no
    amount of replay recovers the exact sentences. The flags-off test asserts
    the two columns the request names, and says so.
    """

    def __init__(self, fixture: dict[str, Any]) -> None:
        super().__init__(fixture)
        v1 = fixture["v1"]
        self._cluster = {rid: rec["Cluster ID"] for rid, rec in v1.items()}
        self._uncertain = {
            rid for rid, rec in v1.items() if rec["Routing"] == "manual_review"
        }

    def _same_cluster(self, left: Iterable[str], right: Iterable[str]) -> bool:
        left_ids = {self._cluster.get(r) for r in left if self._cluster.get(r)}
        right_ids = {self._cluster.get(r) for r in right if self._cluster.get(r)}
        return bool(left_ids & right_ids)

    def _answer_partition(self, payload: dict[str, Any]) -> str:
        signatures = payload.get("signatures", [])
        rows_per_sig = self.index.resolve_all(signatures)
        resolved = {s["signature_id"]: r for s, r in zip(signatures, rows_per_sig)}
        uncertain: list[str] = []
        by_cluster: dict[str, list[str]] = {}
        singletons: list[list[str]] = []
        for sig in signatures:
            sid = sig["signature_id"]
            rows = resolved[sid]
            # An uncertain signature is reported uncertain, never grouped —
            # that is how v1's one merged-but-uncertain row (13364185) got its
            # cluster: uncertain out of the bucketed pass, then merged by the
            # residue pass, which does not clear the flag.
            if rows and all(r in self._uncertain for r in rows):
                uncertain.append(sid)
                continue
            cluster = next((self._cluster[r] for r in sorted(rows) if self._cluster.get(r)), None)
            if cluster is None:
                singletons.append([sid])
            else:
                by_cluster.setdefault(cluster, []).append(sid)
        return json.dumps({
            "entities": [
                {
                    "signature_ids": group,
                    "institution": "",
                    "department": "",
                    "confidence": 0.9,
                    "reasoning": "V1ReplayLLM: replayed from the recorded v1 Cluster ID.",
                }
                for group in list(by_cluster.values()) + singletons
            ],
            "uncertain_signature_ids": uncertain,
        })

    def _answer_pair(self, payload: dict[str, Any]) -> str:
        candidate, entity_rows = self._pair_entries(payload)
        for entity, rows in zip(payload.get("entities", []), entity_rows):
            if self._same_cluster(candidate, rows):
                return json.dumps({
                    "decision": "match",
                    "matched_entity_id": entity["entity_id"],
                    "confidence": 0.9,
                    "reasoning": "V1ReplayLLM: shared the recorded v1 cluster.",
                })
        # Never "uncertain": that would mark BOTH sides uncertain and demote
        # entities the recording shows as routed "cluster".
        return json.dumps({
            "decision": "new",
            "matched_entity_id": None,
            "confidence": 0.9,
            "reasoning": "V1ReplayLLM: not in the recorded v1 cluster.",
        })


# ---------------------------------------------------------------------------
# Running a clustering pass
# ---------------------------------------------------------------------------

async def run_clustering(rows: list[DedupRow], llm: Any) -> tuple[dict[str, Any], Any]:
    """Cluster ``rows`` with ``llm``.

    Returns ``({row_id: DedupResultRow}, DedupSummary)`` — the summary carries
    the LLM-call count the report compares across flag settings.
    """
    from config import Settings
    from dedup.adjudicator import cluster_blocks
    from dedup.signatures import group_rows_by_block

    bind = getattr(llm, "bind_blocks", None)
    if bind is not None:
        bind({
            block_id: {row.row_id for row in members}
            for block_id, members in group_rows_by_block(rows).items()
        })

    response = await cluster_blocks(rows, llm, settings=Settings())
    return {row.row_id: row for row in response.rows}, response.summary


def cluster_of(results: dict[str, Any], row_id: str) -> Optional[str]:
    result = results.get(row_id)
    return None if result is None else result.cluster_id


def describe(results: dict[str, Any], row_ids: Iterable[str]) -> str:
    """A failure message that names cluster, routing and the reasoning behind it."""
    lines = []
    for row_id in row_ids:
        result = results.get(row_id)
        if result is None:
            lines.append(f"  {row_id}: MISSING FROM OUTPUT")
            continue
        reasoning = re.sub(r"\s+", " ", result.reasoning or "").strip()
        lines.append(
            f"  {row_id}: cluster={result.cluster_id} routing={result.routing} "
            f"reasoning={reasoning[:240] or '(empty)'}"
        )
    return "\n".join(lines)
