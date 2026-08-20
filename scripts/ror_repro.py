"""ROR Tier-1 lookup reproduction / diagnosis harness.

Prints, for one input record, exactly what Tier 1 sees and why each ROR
candidate scored what it scored:

  * the raw input Name 1 / Name 2 / city / region / country, pre-preprocessing
  * the exact affiliation string ``call_ror`` builds
  * every affiliation-endpoint candidate — ROR id, canonical name, aliases,
    country, and the score ``_compute_name_score()`` assigns it
  * which scoring branch produced that score (exact / token-subset /
    substring / fuzzy / initialism)
  * for a candidate that did NOT reach 1.0 via a shortcut, which guard blocked
    it — identifier-token, distinctive-token or country
  * whether the query-endpoint fallback ran, with the same detail

Usage
-----
    # replay from the cached fixtures (no network — the default)
    python3 scripts/ror_repro.py
    python3 scripts/ror_repro.py --case A

    # re-record the fixtures against the live ROR API
    python3 scripts/ror_repro.py --record

The recorded fixtures live in ``tests/fixtures/ror_repro/`` and are what
``tests/test_ror_allcaps_repro.py`` runs against, so the regression test
needs no network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rapidfuzz import fuzz  # noqa: E402

from enrichment.tier1_ror import (  # noqa: E402
    _CANONICAL_NAME_TYPES,
    _COMMON_DOMAIN_WORDS,
    _compute_name_score,
    _expand_institution_acronyms,
    _expand_state_abbrevs,
    _extract_identifier_tokens,
    _guard_identifier_tokens,
    _extract_location_tokens,
    _initialism_score,
    _normalise_for_tokens,
    _org_country_code,
)
from utils.text_utils import _fuzzy_token_covers, expand_abbreviations  # noqa: E402

FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "ror_repro"
ROR_BASE = "https://api.ror.org/v2/organizations"
THRESHOLD = 0.8


# ─────────────────────────── fixture record / replay ────────────────────────


def _fixture_path(params: dict[str, str]) -> Path:
    """Stable on-disk name for one ROR request's parameter set."""
    blob = json.dumps(params, sort_keys=True)
    digest = hashlib.sha256(blob.encode()).hexdigest()[:16]
    kind = "affiliation" if "affiliation" in params else "query"
    return FIXTURE_DIR / f"{kind}_{digest}.json"


def fetch(params: dict[str, str], *, record: bool) -> dict[str, Any]:
    """Return the ROR response for *params*, from fixture or from the API."""
    path = _fixture_path(params)
    if not record:
        if not path.exists():
            raise FileNotFoundError(
                f"No fixture for params={params}. Re-record with "
                f"`python3 scripts/ror_repro.py --record`."
            )
        return json.loads(path.read_text())["response"]

    import httpx

    resp = httpx.get(ROR_BASE, params=params, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"params": params, "response": data}, indent=2))
    return data


def load_fixtures() -> list[dict[str, Any]]:
    """Every recorded (params, response) pair."""
    return [json.loads(p.read_text()) for p in sorted(FIXTURE_DIR.glob("*.json"))]


def fixture_handler(request: Any) -> Any:
    """httpx.MockTransport handler serving the recorded ROR responses.

    Lets `call_ror` run its real HTTP path with no network at all, so the
    regression test exercises the whole tier rather than the scorer alone.
    An unrecorded request fails loudly instead of silently returning no
    items — a silent empty response would look exactly like a genuine ROR
    miss and quietly pass a broken test.
    """
    import httpx

    params = dict(request.url.params)
    for fx in load_fixtures():
        if fx["params"] == params:
            return httpx.Response(200, json=fx["response"])
    raise AssertionError(
        f"No recorded ROR fixture for params={params}. Re-record with "
        f"`python3 scripts/ror_repro.py --record`."
    )


# ───────────────────────────── score diagnosis ──────────────────────────────


def diagnose_score(
    query: str,
    org_names: list[dict[str, Any]],
    location_tokens: set[str],
) -> dict[str, Any]:
    """Re-walk ``_compute_name_score`` and report the deciding branch/guard.

    Mirrors the production function step for step and cross-checks its own
    verdict against the real ``_compute_name_score`` (see ``agrees``), so this
    diagnostic cannot silently drift away from the code it explains.
    """
    out: dict[str, Any] = {"branch": None, "blocked_by": None, "score": 0.0}

    query_lower = _normalise_for_tokens(query.strip())
    if not query_lower:
        out["branch"] = "empty-query"
        return out

    canonical_values: list[str] = []
    all_values: list[str] = []
    for n in org_names:
        val = n.get("value")
        if not val:
            continue
        norm = _normalise_for_tokens(val)
        all_values.append(norm)
        if set(n.get("types") or []) & _CANONICAL_NAME_TYPES:
            canonical_values.append(norm)

    # Step 1 — exact match against any variant.
    if query_lower in all_values:
        out.update(branch="exact", score=1.0)
        return _finish(out, query, org_names, location_tokens)

    query_tokens = set(query_lower.split())
    significant = {t for t in query_tokens if len(t) >= 4}
    distinctive = significant - location_tokens
    q_identifiers = _guard_identifier_tokens(query)

    scoring_values = [v for v in all_values if len(v) >= 5]
    if not scoring_values:
        out["branch"] = "no-scorable-variant"
        return _finish(out, query, org_names, location_tokens)

    def _length_ok(a: str, b: str, ratio: float = 0.6) -> bool:
        shorter, longer = min(len(a), len(b)), max(len(a), len(b))
        return longer > 0 and shorter / longer >= ratio

    # Steps 2 + 3 — subset / substring shortcuts against canonical names.
    blocked: list[str] = []
    for val in canonical_values:
        val_tokens = set(val.split())
        if q_identifiers and not q_identifiers.issubset(val_tokens):
            missing = sorted(q_identifiers - val_tokens)
            blocked.append(
                f"identifier-token guard: query acronym(s) {missing} absent "
                f"from '{val}'"
            )
            continue
        if significant and not distinctive:
            blocked.append(
                "distinctive-token guard: query's only significant tokens are "
                "location tokens"
            )
            continue
        if significant and significant.issubset(val_tokens):
            out.update(branch="token-subset", score=1.0)
            return _finish(out, query, org_names, location_tokens)
        if _length_ok(query_lower, val, ratio=0.9) and (
            query_lower in val or val in query_lower
        ):
            out.update(branch="substring", score=1.0)
            return _finish(out, query, org_names, location_tokens)

    # Step 4 — guarded fuzzy token_sort_ratio against canonical names.
    best, best_detail = 0.0, None
    for val in [v for v in canonical_values if len(v) >= 5]:
        raw = fuzz.token_sort_ratio(query_lower, val) / 100.0
        ratio, guard = raw, None
        v_tokens = set(val.split())
        q_distinctive = {
            t for t in query_tokens
            if len(t) >= 5 and t not in _COMMON_DOMAIN_WORDS
            and t not in location_tokens
        }
        uncovered = [
            t for t in sorted(q_distinctive)
            if not any(_fuzzy_token_covers(t, u) for u in v_tokens)
        ]
        if q_distinctive and uncovered:
            ratio = min(ratio, 0.7)
            guard = f"distinctive-token guard: {uncovered} uncovered"
        if q_identifiers and not q_identifiers.issubset(v_tokens):
            ratio = min(ratio, 0.7)
            missing = sorted(q_identifiers - v_tokens)
            g = f"identifier-token guard: {missing} absent"
            guard = f"{guard}; {g}" if guard else g
        if ratio > best:
            best, best_detail = ratio, (val, raw, guard)

    initialism = _initialism_score(query, canonical_values)
    if initialism > best:
        out.update(branch="initialism", score=initialism)
    else:
        out.update(branch="fuzzy", score=best)
        if best_detail:
            val, raw, guard = best_detail
            out["fuzzy_raw"] = round(raw, 4)
            out["fuzzy_against"] = val
            if guard:
                out["blocked_by"] = guard
    if blocked and not out.get("blocked_by"):
        out["blocked_by"] = blocked[0]
    elif blocked:
        out["shortcut_blocked_by"] = blocked[0]
    return _finish(out, query, org_names, location_tokens)


def _finish(
    out: dict[str, Any],
    query: str,
    org_names: list[dict[str, Any]],
    location_tokens: set[str],
) -> dict[str, Any]:
    """Cross-check the diagnosis against the production scorer."""
    actual = _compute_name_score(query, org_names, location_tokens)
    out["actual_score"] = actual
    out["agrees"] = abs(actual - out["score"]) < 1e-9
    return out


# ────────────────────────────── report rendering ────────────────────────────


def _names(org: dict[str, Any]) -> tuple[str, list[str]]:
    canonical, aliases = None, []
    for n in org.get("names") or []:
        if set(n.get("types") or []) & _CANONICAL_NAME_TYPES:
            canonical = canonical or n.get("value")
        else:
            aliases.append(n.get("value"))
    return canonical or "?", aliases


def _report_candidates(
    items: list[dict[str, Any]],
    rescore_names: list[str],
    location_tokens: set[str],
    country_code: str | None,
    *,
    affiliation: bool,
) -> bool:
    """Print every candidate with score, branch and blocking guard.

    Returns True if any candidate would be accepted by the tier.
    """
    accepted = False
    for item in items:
        org = item["organization"] if affiliation else item
        canonical, aliases = _names(org)
        ror_country = _org_country_code(org)
        best_name, best = None, None
        for n in rescore_names:
            d = diagnose_score(n, org.get("names") or [], location_tokens)
            if best is None or d["score"] > best["score"]:
                best, best_name = d, n

        country_ok = (
            country_code is None
            or ror_country is None
            or ror_country.upper() == country_code.upper()
        )
        print(f"  • {org.get('id')}  {canonical!r}")
        print(f"      aliases : {aliases}")
        print(f"      country : {ror_country}  (record wants {country_code})")
        if affiliation:
            print(
                f"      ROR affiliation score : {item.get('score')}  "
                f"chosen={item.get('chosen')}"
            )
        print(
            f"      local   : score={best['score']:.4f} via {best['branch']}"
            f"  (query {best_name!r})"
        )
        if best.get("fuzzy_raw") is not None:
            print(
                f"                fuzzy raw={best['fuzzy_raw']:.4f} against "
                f"{best['fuzzy_against']!r}"
            )
        if best.get("blocked_by"):
            print(f"      BLOCKED : {best['blocked_by']}")
        if best.get("shortcut_blocked_by"):
            print(f"      shortcut blocked: {best['shortcut_blocked_by']}")
        if not country_ok:
            print("      BLOCKED : country guard — wrong country, rejected")
        if not best["agrees"]:
            print(
                f"      !! diagnosis disagrees with _compute_name_score "
                f"({best['actual_score']})"
            )
        verdict_ok = best["score"] >= THRESHOLD and country_ok
        if affiliation:
            verdict_ok = verdict_ok and bool(item.get("chosen"))
        print(f"      VERDICT : {'ACCEPT' if verdict_ok else 'reject'}")
        accepted = accepted or verdict_ok
    return accepted


def run_case(
    label: str,
    name1: str,
    *,
    name2: str | None = None,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    country_code: str | None = None,
    record: bool = False,
) -> bool:
    """Reproduce one Tier-1 ROR lookup end to end. Returns True on a hit."""
    print("=" * 78)
    print(f"CASE {label}")
    print("=" * 78)
    print("Raw input as it entered the pipeline (pre-preprocessing):")
    print(f"  Name 1  : {name1!r}")
    print(f"  Name 2  : {name2!r}")
    print(f"  City    : {city!r}")
    print(f"  Region  : {state!r}")
    print(f"  Country : {country!r}  (ISO {country_code!r})")

    # Mirror call_ror's query construction exactly.
    ror_name = _expand_state_abbrevs(name1)
    aff_parts = [ror_name] + [
        p.strip() for p in (city, state, country) if p and p.strip()
    ]
    affiliation_string = ", ".join(aff_parts)
    location_tokens = _extract_location_tokens(city, state, country)
    expanded_name = expand_abbreviations(name1) or name1
    ror_expanded = expand_abbreviations(ror_name) or ror_name
    rescore_names = list(
        dict.fromkeys([name1, expanded_name, ror_name, ror_expanded])
    )

    print(f"\nAffiliation string sent to ROR: {affiliation_string!r}")
    print(f"Local rescore names           : {rescore_names}")
    print(f"Location tokens               : {sorted(location_tokens)}")
    print(f"Query identifier tokens       : {sorted(_extract_identifier_tokens(name1))}")
    print(f"Normalised query              : {_normalise_for_tokens(name1)!r}")

    print("\n── Strategy A: affiliation endpoint ──")
    data = fetch({"affiliation": affiliation_string}, record=record)
    items = data.get("items", [])
    print(f"{len(items)} candidate(s):")
    hit = _report_candidates(
        items, rescore_names, location_tokens, country_code, affiliation=True
    )
    if hit:
        print("\nRESULT: HIT on the affiliation endpoint (first pass).")
        return True

    acr_name = _expand_institution_acronyms(name1)
    if acr_name.strip().lower() != name1.strip().lower():
        acr_parts = [acr_name] + [
            p.strip() for p in (city, state, country) if p and p.strip()
        ]
        acr_aff = ", ".join(acr_parts)
        print(f"\n── Strategy A2: acronym-expanded affiliation {acr_aff!r} ──")
        data2 = fetch({"affiliation": acr_aff}, record=record)
        items2 = data2.get("items", [])
        print(f"{len(items2)} candidate(s):")
        hit = _report_candidates(
            items2,
            [name1, acr_name, expand_abbreviations(acr_name) or acr_name],
            location_tokens,
            country_code,
            affiliation=True,
        )
        if hit:
            print("\nRESULT: HIT on the acronym-expanded affiliation retry.")
            return True
    else:
        print("\n── Strategy A2: no institution-acronym expansion applies ──")

    print("\n── Strategy B: query endpoint fallback ──")
    qp: dict[str, str] = {"query": ror_name}
    if country_code:
        qp["filter"] = f"locations.geonames_details.country_code:{country_code}"
    qdata = fetch(qp, record=record)
    qitems = qdata.get("items") or []
    if not qitems and country_code:
        print("  (0 items with the country filter — retrying without it)")
        qdata = fetch({"query": ror_name}, record=record)
        qitems = qdata.get("items") or []
    print(f"{len(qitems)} candidate(s):")
    expanded_query = expand_abbreviations(ror_name) or ror_name
    hit = _report_candidates(
        qitems[:10],
        list(dict.fromkeys([expanded_query, name1])),
        location_tokens,
        country_code,
        affiliation=False,
    )
    print(f"\nRESULT: {'HIT' if hit else 'MISS'} on the query endpoint.")
    return hit


# Row 35 of Thesis_Demo_50.xlsx (Customer 40000014) and the controls that
# isolate casing from abbreviation from punctuation.
CASES: dict[str, dict[str, Any]] = {
    "A": {
        "label": "A — row 35 exactly as it arrives from SAP",
        "name1": "BRIGHAM & WOMENS HOSP",
        "name2": "DEPT OF RADIOLOGY / 75 FRANCIS ST",
    },
    "B": {
        "label": "B — the official name in mixed case",
        "name1": "Brigham and Women's Hospital",
    },
    "C": {
        "label": "C — row 35 with casing normalised, otherwise unchanged",
        "name1": "Brigham & Womens Hosp",
    },
    "D": {
        "label": "D — row 35 with HOSP expanded, casing unchanged",
        "name1": "BRIGHAM & WOMENS HOSPITAL",
    },
    "HFT": {
        "label": "HFT — row 9, the case-CONTRAST query the guard protects",
        "name1": "HFT Stuttgart",
        "city": "STUTTGART",
        "state": None,
        "country": "DE",
        "country_code": "DE",
    },
    "FLA": {
        "label": "FLA — row 23, must resolve to Florida State, never Kent State",
        "name1": "FL STATE UNIV.",
        "city": "TALLAHASSEE",
        "state": "FL",
    },
}

_DEFAULTS = {
    "name2": None,
    "city": "BOSTON",
    "state": "MA",
    "country": "US",
    "country_code": "US",
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--record", action="store_true",
        help="hit the live ROR API and refresh the fixtures",
    )
    ap.add_argument(
        "--case", action="append",
        help="run only these cases (A, B, C, D, HFT); default: all",
    )
    args = ap.parse_args()

    wanted = args.case or list(CASES)
    results: dict[str, bool] = {}
    for key in wanted:
        cfg = dict(_DEFAULTS)
        cfg.update(CASES[key])
        label = cfg.pop("label")
        results[key] = run_case(label, record=args.record, **cfg)
        print()

    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for key, hit in results.items():
        print(f"  {key:<4} {'HIT ' if hit else 'MISS'}  {CASES[key]['label']}")


if __name__ == "__main__":
    main()
