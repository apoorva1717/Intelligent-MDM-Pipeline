"""Tests for the offline dedup evaluation harness (eval/dedup_eval.py).

Builds a scored workbook in memory with known ground-truth vs predicted columns
and asserts every metric exactly. No network, no LLM.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.dedup_eval import (
    business_risk_metrics,
    election_metrics,
    evaluate,
    load_scored_rows,
    pairwise_metrics,
)

_HEADERS = [
    "Customer", "expected_cluster", "expected_routing", "Cluster ID",
    "Routing", "is_golden_record", "election_status", "score_final",
]

# (customer, exp_cluster, exp_routing, cluster_id, routing, is_golden, status, score)
_ROWS = [
    ("1", "A1", "cluster", "cA", "cluster", True, "proposed", 30),
    ("2", "A1", "cluster", "cA", "cluster", False, "proposed", 20),
    ("3", "A1", "cluster", None, "unique", True, "unique", 10),      # FN + competing golden
    ("9", "unique", "unique", "cB", "cluster", False, "proposed", 5),  # wrongful block
    ("8", "unique", "unique", "cB", "cluster", True, "proposed", 40),  # bad-merge winner
    ("20", "M1", "manual_review", "cM", "cluster", True, "proposed", 15),  # upgrade + tie
    ("21", "M1", "manual_review", "cM", "cluster", False, "proposed", 15),  # upgrade + tie
]


def _build(path):
    wb = Workbook()
    ws = wb.active
    ws.append(_HEADERS)
    for row in _ROWS:
        ws.append(list(row))
    wb.save(path)


def test_load_scored_rows(tmp_path):
    p = tmp_path / "scored.xlsx"
    _build(p)
    rows = load_scored_rows(p)
    assert len(rows) == 7
    assert rows[0]["row_id"] == "1"
    assert rows[2]["cluster_id"] is None            # blank cluster id
    assert rows[3]["is_golden_record"] is False


def test_pairwise_metrics(tmp_path):
    p = tmp_path / "s.xlsx"
    _build(p)
    rows = load_scored_rows(p)
    m = pairwise_metrics(rows)
    # GT pairs: A1{1,2,3}=3 + M1{20,21}=1 = 4; predicted: cA{1,2}, cB{8,9}, cM{20,21}=3
    assert m["ground_truth_pairs"] == 4
    assert m["predicted_pairs"] == 3
    assert m["true_positives"] == 2   # (1,2) and (20,21)
    assert m["false_positives"] == 1  # (8,9) — the wrong merge
    assert m["false_negatives"] == 2  # (1,3), (2,3) — the missed pairs
    assert m["precision"] == round(2 / 3, 4)
    assert m["recall"] == 0.5


def test_business_risk_metrics(tmp_path):
    p = tmp_path / "s.xlsx"
    _build(p)
    rows = load_scored_rows(p)
    b = business_risk_metrics(rows)
    assert b["wrongful_block_candidates"] == {"count": 1, "row_ids": ["9"]}
    assert b["competing_goldens"] == {"count": 1, "row_ids": ["3"]}
    assert b["uncertainty_upgrades"] == {"count": 2, "row_ids": ["20", "21"]}


def test_election_metrics(tmp_path):
    p = tmp_path / "s.xlsx"
    _build(p)
    rows = load_scored_rows(p)
    e = election_metrics(rows)
    assert e["clusters"] == 3           # cA, cB, cM
    assert e["elections"] == 3          # one golden per predicted cluster
    assert e["manual_review_rows"] == 0
    assert e["tiebreak_decided_clusters"] == {"count": 1, "cluster_ids": ["cM"]}


def test_evaluate_assembles_full_report(tmp_path):
    p = tmp_path / "s.xlsx"
    _build(p)
    report = evaluate(p)
    assert report["rows_evaluated"] == 7
    assert set(report) == {
        "source", "rows_evaluated", "weights_versions",
        "pairwise", "business_risk", "election",
    }
