"""Ticket 19 - is a domain-equal ROR hit the record's organisation, or its parent?

The domain-equality test (ticket 15's method) is **circular** for the
domain-first strategy: that strategy *retrieves* the organisation whose
registered domain equals the record's, so domain equality holds by
construction.  It is also blind to the failure mode that dominates this
population - a site, department, medical center or subsidiary shares its
parent's domain, so the parent is what comes back.

Every domain-equal ROR hit is therefore re-adjudicated here on evidence the
retrieval did not use.  Two independent axes, both computed:

**Axis 1 - would the pipeline's own name comparator accept this pairing?**
``registry_match.names_agree(.., 88)`` or ``names_agree_by_containment`` - the
same pair ``consistency.py`` uses.

**Axis 2 - token-set direction**, on ``registry_match.distinctive_tokens`` with
ROR's bracketed keyspace qualifier stripped:

    equal        the two names carry the same distinctive tokens
    ror_broader  ROR's tokens are a strict subset of the record's
                 ("ExxonMobil" for "ExxonMobil Refinery") - ROR is naming a
                 broader organisation than the record does
    ror_narrower the record's tokens are a strict subset of ROR's
                 ("SLAC National Accelerator" -> "... Laboratory") - a truncated
                 SAP name, same entity
    overlapping  some shared tokens, each has its own
    disjoint     no shared distinctive token at all
                 ("Vamc Miami Visn 8" -> "United States Department of Veterans
                 Affairs")

``ror_broader``, ``overlapping`` and ``disjoint`` all mean ROR answered with a
*different* legal entity from the one the record names.  Writing that
``ror_id`` onto the record is a wrong identifier, which ticket 02 records as
strictly worse than none.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / ".scratch/agentic-enrichment/scripts"))

from q19_report import load, verdicts  # noqa: E402
from utils.text_utils import strip_parentheticals  # noqa: E402
from enrichment.registry_match import (  # noqa: E402
    distinctive_tokens,
    names_agree,
    names_agree_by_containment,
)


_US = {"us", "usa", "u", "s", "united", "states", "unitedstates"}


def _canon(tokens):
    """Collapse the only surface difference that shows up in this population:
    "US" / "U.S." / "United States" naming the same country prefix.  Measured,
    not assumed - it is what separates 'US Department of Veterans Affairs' from
    'United States Department of Veterans Affairs'."""
    out = set()
    for t in tokens:
        t = t.lower()
        out.add("_us_" if t in _US else t)
    return out


def axes(record_name, ror_name):
    ror_name = strip_parentheticals(ror_name or "") or ""
    if not record_name or not ror_name:
        return (None, "unknown")
    accepts = (names_agree(record_name, ror_name, 88.0)
               or names_agree_by_containment(record_name, ror_name))
    rec = _canon(distinctive_tokens(record_name))
    ror = _canon(distinctive_tokens(ror_name))
    if not rec or not ror:
        direction = "unknown"
    elif rec == ror:
        direction = "equal"
    elif ror < rec:
        direction = "ror_broader"
    elif rec < ror:
        direction = "ror_narrower"
    elif rec & ror:
        direction = "overlapping"
    else:
        direction = "disjoint"
    return (accepts, direction)


SAME = {"equal", "ror_narrower"}


def main():
    lost, results = load()
    print("=== parent-vs-entity re-adjudication of every domain-equal ROR hit ===")
    print()
    for strat in ("control", "raw", "nosuffix", "name1_name2", "domain_first"):
        per = results.get(strat)
        if not per:
            continue
        v = verdicts(lost, per)
        cross = Counter()
        rows = []
        for k, t in v.items():
            if t[0] != "correct":       # domain-equal ROR hits only
                continue
            q = lost[k]["control"]
            nm = per[k]["ror"].get("official_name") or ""
            a, d = axes(q, nm)
            cross[(a, d)] += 1
            rows.append((d, lost[k]["corpus"], q, nm, lost[k]["domain"], a))
        if not cross:
            continue
        n = sum(cross.values())
        same = sum(c for (a, d), c in cross.items() if d in SAME)
        print("%-14s domain-equal ROR hits = %d" % (strat, n))
        print("     axis 2 - token direction: %s"
              % dict(Counter(d for (a, d) in
                             [kk for kk, c in cross.items() for _ in range(c)])))
        print("     axis 1 - pipeline comparator accepts the pairing: %d of %d"
              % (sum(c for (a, d), c in cross.items() if a), n))
        print("     ==> SAME entity (equal or record-is-a-truncation): %d"
              % same)
        print("     ==> DIFFERENT entity (ROR broader / overlapping / disjoint): %d"
              % (n - same))
        if strat == "domain_first":
            print()
            print("     every hit where ROR named a different entity:")
            for d, corpus, q, nm, dom, a in sorted(rows):
                if d in SAME:
                    continue
                print("       %-12s %-3s %-36s dom=%-15s -> %-44s ctl-accepts=%s"
                      % (d, corpus, repr(q)[:36], str(dom)[:15],
                         repr(strip_parentheticals(nm))[:44], a))
        print()


if __name__ == "__main__":
    main()
