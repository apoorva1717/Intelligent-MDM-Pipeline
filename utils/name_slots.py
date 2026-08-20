"""The canonical name-slot vocabulary.

Every rule that walks the SAP name block imports its slot list from here
instead of writing a literal ``("name1", "name2", ...)`` tuple. One
definition means a rule can never silently apply to a subset of the block:
adding a slot below extends every rule that uses these constants at once.

Three lists, because the rules genuinely differ in shape:

``NAME_SLOTS``
    The whole block, Name 1 first. Rules that treat every name field the
    same (junk stripping, opaque-code removal, contact extraction, casing,
    abbreviation expansion) use this.

``DEPT_SLOTS``
    The block minus Name 1. Name 1 holds the organisation; Names 2..N hold
    departments, units and overflow. Rules that route or clean *unit* text
    — department detection, leftward packing, named-building moves — use
    this, and must not touch Name 1.

``ADJACENT_NAME_PAIRS``
    Consecutive (upper, lower) slot pairs across the whole block. Rules
    about one field continuing into or duplicating the next — overflow,
    adjacent-duplicate, blank-gap — use this rather than hard-coding the
    Name 1 / Name 2 pair.
"""

from __future__ import annotations

# Number of SAP name columns carried through the pipeline. Raising this is
# the single edit that widens the whole name block: add the matching
# ``name_N`` field to ``EnrichmentRecord``/``EnrichmentResult`` and the
# ``name{N}_enriched`` entry to RESPONSE_COLUMNS, and every rule below
# follows automatically.
NAME_SLOT_COUNT = 5

# "name1" … "name5" — pipeline-internal attribute names.
NAME_SLOTS: tuple[str, ...] = tuple(
    f"name{i}" for i in range(1, NAME_SLOT_COUNT + 1)
)

# "name_1" … "name_5" — the EnrichmentRecord (SAP column) attribute names.
RECORD_NAME_FIELDS: tuple[str, ...] = tuple(
    f"name_{i}" for i in range(1, NAME_SLOT_COUNT + 1)
)

# "name1_enriched" … — the EnrichmentResult output attribute names.
ENRICHED_NAME_FIELDS: tuple[str, ...] = tuple(
    f"{slot}_enriched" for slot in NAME_SLOTS
)

# Everything below Name 1: the department / unit / overflow slots.
DEPT_SLOTS: tuple[str, ...] = NAME_SLOTS[1:]

DEPT_ENRICHED_FIELDS: tuple[str, ...] = tuple(
    f"{slot}_enriched" for slot in DEPT_SLOTS
)

# ("name1", "name2"), ("name2", "name3"), … — upper slot first.
ADJACENT_NAME_PAIRS: tuple[tuple[str, str], ...] = tuple(
    (NAME_SLOTS[i], NAME_SLOTS[i + 1]) for i in range(len(NAME_SLOTS) - 1)
)

ADJACENT_RECORD_NAME_PAIRS: tuple[tuple[str, str], ...] = tuple(
    (RECORD_NAME_FIELDS[i], RECORD_NAME_FIELDS[i + 1])
    for i in range(len(RECORD_NAME_FIELDS) - 1)
)

# "Name 1" … — the SAP column headers, keyed by internal slot name.
NAME_SLOT_LABELS: dict[str, str] = {
    slot: f"Name {i}" for i, slot in enumerate(NAME_SLOTS, start=1)
}


def name_values(record: object) -> list[str | None]:
    """Every name slot of *record*, in block order.

    Accepts anything exposing either the ``nameN`` accessors (pipeline
    objects, ``PreprocessResult``) or the ``name_N`` SAP fields
    (``EnrichmentRecord``), so callers on both sides of the boundary share
    one helper.
    """
    values: list[str | None] = []
    for slot, field in zip(NAME_SLOTS, RECORD_NAME_FIELDS):
        if hasattr(record, slot):
            values.append(getattr(record, slot))
        else:
            values.append(getattr(record, field, None))
    return values


def slot_label(slot: str) -> str:
    """Render an internal slot name as its SAP column header."""
    return NAME_SLOT_LABELS.get(slot, slot)
