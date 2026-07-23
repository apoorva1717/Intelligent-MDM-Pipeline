"""Unit tests for the pure candidate-nomination logic (dedup/candidates.py).

No LLM, no network — deterministic string arithmetic only.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dedup.candidates import (
    CandidateUnit,
    generate_candidate_pairs,
    nominate,
    strip_legal_suffix,
)

TH = {"name_threshold": 0.85, "token_threshold": 0.6}


def _u(index, name, *, lei=None, ror=None, n2=False, adj=True):
    return CandidateUnit(index, name, ror, lei, n2, adj)


# ---------------------------------------------------------------------------
# Suffix stripping
# ---------------------------------------------------------------------------

class TestSuffixStrip:
    def test_trailing_suffix_stripped(self):
        assert strip_legal_suffix("Pfizer AG") == "pfizer"
        assert strip_legal_suffix("Pfizer Inc.") == "pfizer"
        assert strip_legal_suffix("Bayer Aktiengesellschaft") == "bayer"

    def test_only_trailing_token_stripped(self):
        # A suffix word that is NOT trailing is kept.
        assert strip_legal_suffix("AG Berlin Services") == "ag berlin services"
        assert strip_legal_suffix("Berlin Services AG") == "berlin services"

    def test_case_insensitive_and_punctuation_tolerant(self):
        assert strip_legal_suffix("Roche g.m.b.h.") == "roche"
        assert strip_legal_suffix("Muster A/S") == "muster"
        assert strip_legal_suffix("Acme B.V.") == "acme"

    def test_repeated_trailing_suffixes(self):
        assert strip_legal_suffix("Muster GmbH & Co. KG") == "muster"

    def test_name_that_is_only_a_suffix_falls_back(self):
        # Stripping would empty it → keep the normalised full name.
        assert strip_legal_suffix("AG") == "ag"


# ---------------------------------------------------------------------------
# Nomination rules
# ---------------------------------------------------------------------------

class TestNominate:
    def test_rule1_same_lei(self):
        c = nominate(_u(0, "Pfizer AG", lei="L1"), _u(1, "Pfizer Inc.", lei="L1"), **TH)
        assert c is not None and c.rule == "id" and c.score == 1.0

    def test_rule1_same_ror(self):
        c = nominate(_u(0, "Uni X", ror="R1"), _u(1, "University X", ror="R1"), **TH)
        assert c is not None and c.rule == "id"

    def test_rule1_priority_over_name(self):
        # Same LEI AND near-identical name → id wins (priority).
        c = nominate(_u(0, "Pfizer AG", lei="L1"), _u(1, "Pfizer Inc.", lei="L1"), **TH)
        assert c.rule == "id"

    def test_rule2_suffix_stripped_name(self):
        c = nominate(_u(0, "Pfizer AG"), _u(1, "Pfizer Inc."), **TH)
        assert c is not None and c.rule == "name" and c.score >= 0.85

    def test_rule3_token_word_order(self):
        c = nominate(
            _u(0, "Cancer Research Institute"),
            _u(1, "Institute of Cancer Research"), **TH,
        )
        assert c is not None and c.rule == "token" and c.score >= 0.6

    def test_no_signal_not_nominated(self):
        assert nominate(_u(0, "Acme Metals"), _u(1, "Globex Systems"), **TH) is None

    def test_different_lei_is_not_a_convergence(self):
        # Different non-empty LEIs do not converge (and names differ) → no nom.
        assert nominate(
            _u(0, "Acme Metals", lei="L1"), _u(1, "Globex Systems", lei="L2"), **TH
        ) is None


# ---------------------------------------------------------------------------
# Candidate generation: eligibility, ordering, determinism, cap priority
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_same_bucket_both_adjudicated_not_eligible(self):
        # Already compared by Mode A/B → not residue.
        units = [_u(0, "Pfizer AG", lei="L1", n2=False, adj=True),
                 _u(1, "Pfizer Inc.", lei="L1", n2=False, adj=True)]
        assert generate_candidate_pairs(units, **TH) == []

    def test_cross_boundary_is_eligible(self):
        units = [_u(0, "Pfizer AG", lei="L1", n2=False, adj=True),
                 _u(1, "Pfizer Inc.", lei="L1", n2=True, adj=True)]
        cands = generate_candidate_pairs(units, **TH)
        assert len(cands) == 1 and cands[0].rule == "id"

    def test_unadjudicated_singleton_is_eligible(self):
        units = [_u(0, "Pfizer AG", lei="L1", n2=False, adj=False),
                 _u(1, "Pfizer Inc.", lei="L1", n2=False, adj=True)]
        assert len(generate_candidate_pairs(units, **TH)) == 1

    def test_priority_ordering_id_then_name_then_token(self):
        units = [
            _u(0, "Cancer Research Institute", n2=True, adj=False),
            _u(1, "Institute of Cancer Research", n2=False, adj=False),  # token
            _u(2, "Pfizer AG", lei="L1", n2=True, adj=False),
            _u(3, "Pfizer Inc.", lei="L1", n2=False, adj=False),         # id
            _u(4, "Roche AG", n2=True, adj=False),
            _u(5, "Roche Inc.", n2=False, adj=False),                    # name
        ]
        rules = [c.rule for c in generate_candidate_pairs(units, **TH)]
        assert rules[0] == "id"          # id-convergence first
        assert "token" in rules and "name" in rules
        assert rules.index("name") < rules.index("token")  # name before token

    def test_determinism_under_shuffle(self):
        base = [
            _u(0, "Pfizer AG", lei="L1", n2=True, adj=False),
            _u(1, "Pfizer Inc.", lei="L1", n2=False, adj=False),
            _u(2, "Roche AG", n2=True, adj=False),
            _u(3, "Roche GmbH", n2=False, adj=False),
            _u(4, "Acme Metals", n2=False, adj=False),
        ]
        want = [(c.a, c.b, c.rule) for c in generate_candidate_pairs(base, **TH)]
        rng = random.Random(99)
        for _ in range(10):
            shuffled = base[:]
            rng.shuffle(shuffled)
            # Re-index so indices stay 0..n-1 but content order varies.
            reindexed = [
                CandidateUnit(i, u.name, u.ror_id, u.lei_id, u.has_name2, u.adjudicated)
                for i, u in enumerate(shuffled)
            ]
            got = generate_candidate_pairs(reindexed, **TH)
            # Same set of (name-pair, rule) regardless of order.
            want_names = sorted(
                (tuple(sorted((base[a].name, base[b].name))), r) for a, b, r in want
            )
            got_names = sorted(
                (tuple(sorted((reindexed[c.a].name, reindexed[c.b].name))), c.rule)
                for c in got
            )
            assert want_names == got_names
