"""Item 4: the pipe-delimited street splitter must classify each segment
individually and route ONLY organisation/department segments to the Name block,
cleaning the source street field. A c/o line, a named building and the street
address stay in the street for the late address stage — they are never routed to
a Name slot (the old splitter routed an unsplit blob to a Name and left the org
in the street).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrichment.preprocess import preprocess_record, _split_pipe_street


def _pp(street1, name1="Acme Corp"):
    return preprocess_record(
        name1=name1, name2=None, name3=None, contact=None, email=None,
        street1=street1, street2=None, street3=None,
    )


class TestPipeSegmentClassification:
    def test_row212_org_to_name_rest_kept_in_street(self):
        # 90000337: only the org moves to a Name slot; the c/o line, the
        # buildings and the address stay in the street (pipes dropped) for the
        # address stage to place.
        r = _pp(
            "MRC - Medical Research Council, | C/O RCUK SSC Ltd, | "
            "Polaris House, | North Star House, | North Star Avenue"
        )
        # Routed org is acronym-deduped ("MRC - Medical Research Council" →
        # "Medical Research Council").
        assert r.name2 == "Medical Research Council"
        assert "|" not in (r.street1 or "")
        # c/o, buildings and address remain in the street, NOT in a Name slot.
        assert r.street1 == "C/O RCUK SSC Ltd, Polaris House, North Star House, North Star Avenue"
        assert not (r.name3 and r.name3.strip())

    def test_care_of_segment_not_routed_to_name(self):
        out = _split_pipe_street("C/O RCUK SSC Ltd | North Star Avenue")
        # No org segment → nothing to route → None (left for the address stage).
        assert out is None

    def test_org_and_address_split_source_cleaned(self):
        # FDA-style hierarchy: every org/dept segment routes to a Name slot; the
        # address stays in the street with the pipes stripped.
        r = _pp(
            "Bioanalytical Methods Branch | Division of Bioanalytical Chemistry | "
            "U.S. Food and Drug Administration | 5100 Paint Branch Pkwy",
            name1=None,
        )
        assert r.street1 == "5100 Paint Branch Pkwy"
        assert "|" not in (r.street1 or "")
        names = [r.name1, r.name2, r.name3, r.name4]
        assert "U.S. Food and Drug Administration" in names
        assert "Division of Bioanalytical Chemistry" in names

    def test_building_segment_stays_in_street(self):
        out = _split_pipe_street("Chemistry Bldg | 6100 Main St | Some Institute")
        assert out is not None
        remainder, org_parts = out
        assert org_parts == ["Some Institute"]
        assert "Chemistry Bldg" in remainder
        assert "6100 Main St" in remainder
