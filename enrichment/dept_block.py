"""The department block — Name 2..5 — as one value with one authority.

`record_type` has `_classify_record`; Name 1 has `name_gate.evaluate`. The
department block had ten writers, two different equivalence tests (UC 12's
`_equiv`, ratio >= 92; finalise's dedup, ratio >= 92 OR `introduces_nothing_new`),
three different skip-canonicalisation tests, and slot origin tracked in six
ad-hoc markers. Nothing owned the block, so nothing could answer the two
questions the block actually raises:

* are these two slots the same unit, written twice? — the Wayne State row
  states `Wayne State University Dept of Biologica` / `Dept of Biological
  Sciences` / `Greenberg Lab`, and the truncated third slot shipped as
  `Department of Biologica`, a unit that does not exist;
* where did this slot's value come from? — which is what says whether a tier
  may still canonicalise it, and it was spread across `preprocess_populated`,
  `_names_from_street`, `_registry_name_fields`, `_ev_name_verdict`,
  `_{slot}_from_tier3` and `_dba_values`.

This module answers both, as pure functions over a list. It writes nothing,
calls no model and does no I/O: `result` is read only for
`has_no_canonical_form`'s address-token comparison, and may be None.
"""

from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

from enrichment.search_terms import has_no_canonical_form, identifies_nothing
from utils.name_identity import introduces_nothing_new
from utils.text_utils import (
    UNIT_SLOT_RANK,
    canonicalise_unit_name,
    is_admin_unit,
    is_granular_unit,
    ordered_unit_word,
)

# ── Origins ───────────────────────────────────────────────────────────────────
#
# Where the value in a slot came from. One vocabulary, replacing the six
# markers. The origin travels WITH THE VALUE — never with the slot — so a
# packed or reordered block still says what produced each value.

#: The record stated it, in this slot, and nothing has rewritten it.
ORIGIN_INPUT = "input"
#: UC 16 lifted it out of Name 1 ("University of Miami Department of
#: Chemistry" -> Name 1 + Name 2).
ORIGIN_SPLIT = "preprocess:split"
#: The street->name router lifted it out of a street slot.
ORIGIN_STREET = "preprocess:street"
#: It was stated somewhere in the name block and preprocessing moved it — the
#: UC 14 leftward pack, or any other slot shift.
ORIGIN_MOVED = "preprocess:moved"
#: ROR or GLEIF spelled it.
ORIGIN_REGISTRY = "registry"
#: A model produced it (Tier 2 canonicalisation, Tier 3 suggestion).
ORIGIN_LLM = "llm"
#: The grounded resolver produced it.
ORIGIN_GROUNDED = "grounded"

#: Origins that mean an AUTHORITY has already answered for this slot. A value
#: with one of these is settled; a value with any other origin is the record's
#: own words, moved or not, and is still a question.
RESOLVED_ORIGINS = frozenset({ORIGIN_REGISTRY, ORIGIN_LLM, ORIGIN_GROUNDED})

#: Every origin this module knows. Used to reject a typo'd origin at the door
#: rather than let it silently read as "not resolved".
ORIGINS = frozenset({
    ORIGIN_INPUT, ORIGIN_SPLIT, ORIGIN_STREET, ORIGIN_MOVED,
    ORIGIN_REGISTRY, ORIGIN_LLM, ORIGIN_GROUNDED,
})

#: The equivalence threshold, carried over from UC 12 and finalise, which both
#: used it and neither shared. Strict enough that "Physics" and "Physiology"
#: are two units (ratio 82) and loose enough that a one-character typo is one.
RATIO_THRESHOLD = 92


def _norm(value: Any) -> str:
    """The canonical unit form, whitespace-collapsed and lowered.

    The comparison key both old passes used: `canonicalise_unit_name` first, so
    "Department of Main Receiving", "Main Receiving Department" and "Main
    Receiving Dept" are one string before anything is compared.
    """
    if not value or not str(value).strip():
        return ""
    text = str(value)
    canon = canonicalise_unit_name(text) or text
    return re.sub(r"\s+", " ", canon.strip()).lower()


def same_unit(a: str | None, b: str | None) -> bool:
    """True when *a* and *b* name the same unit.

    Three ways to be the same unit, and the block needs all three:

    * the canonical forms are equal — the surface-variant case UC 12 handled;
    * they score >= 92 — the typo case ("Main Receiving" / "Main Receivingt");
    * one accounts for every word of the other — the TRUNCATION case the ratio
      cannot see. "Department of Biologica" and "Department of Biological
      Sciences" score 82, under the threshold, which is how the fragment
      shipped in Name 4.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or fuzz.ratio(na, nb) >= RATIO_THRESHOLD:
        return True
    return (
        introduces_nothing_new(str(a), str(b))
        or introduces_nothing_new(str(b), str(a))
    )


def _is_fuller(value: str | None, kept: str | None) -> bool:
    """True when *value* is the completed form of the fragment *kept*.

    One-directional on purpose: *kept* must add nothing to *value*, and *value*
    must add something to *kept*. Two values that account for each other are
    the same text, and neither is the fuller one.
    """
    if not (value and kept):
        return False
    # An admin desk has no fuller form. "Accounts Payable Dept" accounts for
    # every word of "Accounts Payable" and adds one of its own, which is the
    # arithmetic of a completed fragment and the opposite of the truth: the
    # desk is called "Accounts Payable", and the suffix is what UC 6 removes.
    if is_admin_unit(str(kept)):
        return False
    return (
        introduces_nothing_new(str(value), str(kept))
        and not introduces_nothing_new(str(kept), str(value))
    )


def classify(value: str, result: dict | None = None) -> str:
    """What kind of thing *value* names.

    ``'empty'``
        Nothing, or whitespace.
    ``'admin'``
        A back-office desk — "Accounts Payable", "Office of Purchasing". It
        has no canonical form to reach for because it IS the canonical form.
    ``'identifies_nothing'``
        A phrase built entirely of facility functions and scope qualifiers —
        "Central Warehouse" names no unit for an institution to spell.
    ``'granular'``
        A lab, group, centre or facility. Real, named, and below the level a
        registry publishes.
    ``'unit'``
        Everything else: a department, division, school or faculty — the shape
        a canonicaliser and a registry both have an opinion about.

    The first two are the two halves of `has_no_canonical_form`, which is the
    predicate Tier 2, the search terms, the flags and (since the admin-desk
    fix) finalise all ask. Naming them apart costs nothing and lets a caller
    log WHICH answer it got.
    """
    if not value or not str(value).strip():
        return "empty"
    text = str(value).strip()
    if not has_no_canonical_form(text, result):
        return "granular" if is_granular_unit(text) else "unit"
    # The union's two halves, in the order `has_no_canonical_form` asks them.
    if is_admin_unit(text):
        return "admin"
    if identifies_nothing(text, result):
        return "identifies_nothing"
    # Unreachable while `has_no_canonical_form` is the union of exactly those
    # two, and not worth an assert: if it ever grows a third arm, a value that
    # has no canonical form is still not a unit.
    return "identifies_nothing"


def normalise(
    block: list[str | None],
    origins: list[str],
    result: dict | None = None,
) -> tuple[list[str | None], list[str], list[dict]]:
    """Settle one department block. Pure — nothing is written, nothing is read
    but *result*'s address tokens.

    Four steps, in this order, each one a rule that used to live in a
    different module:

    1. **empty** — a whitespace-only slot is an empty slot.
    2. **dedup** — walk the slots in order; a value that :func:`same_unit`\\ s
       one already kept is dropped, UNLESS it is the fuller form of it, in
       which case it replaces the fragment. The replacing value keeps its own
       origin when that origin is an authority's, and otherwise inherits the
       fragment's — a truncated input completed by ROR is ROR's value, and a
       truncated ROR value completed by nothing is still ROR's.
    3. **pack** — survivors move up into the holes, origins travelling with
       them.
    4. **order** — among the slots holding a ranked construction only, a
       division is written above a department. A stable sort, so equal ranks
       keep the order the tiers produced, and a slot holding anything else
       keeps the position packing gave it.

    Returns ``(block, origins, log)``. Every drop, replace and move appends
    ``{'step', 'slot', 'value', 'reason', 'kept'}`` to *log*, where ``slot`` is
    the index the value occupied when the step acted on it.
    """
    if len(origins) != len(block):
        raise ValueError(
            f"origins has {len(origins)} entries for a block of {len(block)}"
        )
    values: list[Any] = list(block)
    origin: list[str] = list(origins)
    log: list[dict] = []

    # 1. Empty is empty.
    for i, val in enumerate(values):
        if val is not None and not str(val).strip():
            log.append({
                "step": "empty", "slot": i, "value": val,
                "reason": "whitespace-only", "kept": None,
            })
            values[i] = None

    # 2. Dedup, in slot order. `kept` holds (index, value) so a replacement
    #    lands back in the slot the fragment occupied — packing, below, is what
    #    moves it, and doing both here would lose the log's slot numbers.
    kept: list[int] = []
    for i, val in enumerate(values):
        if val is None:
            continue
        match = next((k for k in kept if same_unit(values[k], val)), None)
        if match is None:
            kept.append(i)
            continue
        if _is_fuller(val, values[match]):
            log.append({
                "step": "replace", "slot": match, "value": values[match],
                "reason": f"completed-by slot {i}", "kept": val,
            })
            if origin[i] not in RESOLVED_ORIGINS:
                origin[i] = origin[match]
            values[match], origin[match] = val, origin[i]
            values[i] = None
            continue
        log.append({
            "step": "drop", "slot": i, "value": val,
            "reason": f"covered-by slot {match}", "kept": values[match],
        })
        values[i] = None

    # 3. Pack leftward. The origin travels with its value. `dest` is the count
    #    of survivors above this one, which is where it lands — computed from
    #    the position, never by looking the value up, so two slots holding the
    #    same string could not confuse it.
    packed: list[tuple[Any, str]] = []
    for i, val in enumerate(values):
        if val is None:
            continue
        dest = len(packed)
        if dest != i:
            log.append({
                "step": "pack", "slot": i, "value": val,
                "reason": f"moved to slot {dest}", "kept": val,
            })
        packed.append((val, origin[i]))
    values = [v for v, _o in packed] + [None] * (len(block) - len(packed))
    origin = [o for _v, o in packed] + [ORIGIN_INPUT] * (len(block) - len(packed))

    # 4. Unit ordering, among the ranked slots only. The permutation, not the
    #    sorted values — the index is the tiebreak, which is what makes it
    #    stable.
    ranked = [i for i, v in enumerate(values) if ordered_unit_word(v)]
    if len(ranked) > 1:
        order = sorted(
            range(len(ranked)),
            key=lambda k: (UNIT_SLOT_RANK[ordered_unit_word(values[ranked[k]])], k),
        )
        if order != sorted(order):
            current = [(values[i], origin[i]) for i in ranked]
            for dest, source in enumerate(order):
                if dest == source:
                    continue
                log.append({
                    "step": "order", "slot": ranked[source],
                    "value": current[source][0],
                    "reason": f"ranked above slot {ranked[dest]}",
                    "kept": current[source][0],
                })
            for dest, source in enumerate(order):
                values[ranked[dest]], origin[ranked[dest]] = current[source]

    return values, origin, log
