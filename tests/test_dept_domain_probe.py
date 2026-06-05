"""Department-domain candidate matching, incl. abbreviated subdomains
(chem.ufl.edu for "Department of Chemistry")."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from enrichment.orchestrator import _seg_matches_needle, _score_dept_candidate


@pytest.mark.parametrize("seg, needle, expected", [
    ("chem", "chemistry", True),    # abbreviation
    ("phys", "physics", True),
    ("math", "mathematics", True),
    ("csail", "cs", True),          # acronym is a substring of the host
    ("bio", "chemistry", False),    # unrelated
    ("sci", "computer", False),
    ("", "chemistry", False),
])
def test_seg_matches_needle(seg, needle, expected):
    assert _seg_matches_needle(seg, needle) is expected


def test_abbreviated_subdomain_scores_positive():
    # chem.ufl.edu for "Department of Chemistry" must score > 0 (was 0 before).
    score = _score_dept_candidate(
        host="chem.ufl.edu", base="ufl.edu", path="/",
        title="Department of Chemistry", tokens={"chemistry"}, acronym=None,
    )
    assert score >= 3


def test_unrelated_subdomain_scores_zero():
    score = _score_dept_candidate(
        host="bio.ufl.edu", base="ufl.edu", path="/",
        title="Department of Biology", tokens={"chemistry"}, acronym=None,
    )
    assert score == 0
