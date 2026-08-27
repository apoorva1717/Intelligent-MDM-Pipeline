"""Tests for the single domain write path (utils/domain_resolver.py).

Covers both halves of the fix: canonical form (a deep ROR link and a sub-site
host both reduce to the registrable domain, while a department domain keeps its
subdomain) and ownership (a candidate that satisfies none of the four
conditions is rejected outright, and a consumer mailbox never corroborates one).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.domain_resolver import (  # noqa: E402
    DomainEvidence,
    canonicalise_domain,
    canonicalise_host,
    country_conflict,
    email_domain,
    is_generic_email_domain,
    name_similarity,
    resolve_domain,
)

THRESHOLD = 82.0


def _resolve(candidate, **evidence):
    return resolve_domain(
        candidate,
        DomainEvidence(**evidence),
        threshold=THRESHOLD,
        guard_enabled=True,
    )


class TestCanonicalDomain:
    """`domain` is the registrable domain — no scheme, path, query, fragment,
    trailing slash or subdomain."""

    @pytest.mark.parametrize(
        "url, expected",
        [
            # A deep ROR link canonicalises to the registrable domain.
            ("http://www.uni-stuttgart.de/home/index.en.html", "uni-stuttgart.de"),
            ("https://www.mayoclinic.org/patient-visitor-guide/florida", "mayoclinic.org"),
            ("http://www.siemens.com/entry/cc/en/", "siemens.com"),
            # A sub-site host collapses to the registrable domain.
            ("https://investors.lockheedmartin.com", "lockheedmartin.com"),
            ("https://admission.gatech.edu", "gatech.edu"),
            ("https://web.mit.edu", "mit.edu"),
            ("https://rally.massgeneralbrigham.org", "massgeneralbrigham.org"),
            ("https://toxaccess.redwoodtoxicology.com", "redwoodtoxicology.com"),
            # Trailing slash, query, fragment, port, case, bare host.
            ("http://www.usf.edu/", "usf.edu"),
            ("https://example.com/page?q=1#top", "example.com"),
            ("HTTPS://WWW.Example.COM:8443/x", "example.com"),
            ("lockheedmartin.com", "lockheedmartin.com"),
            # Two-part TLDs keep their registrable form.
            ("https://www.dur.ac.uk/faculty/handbook", "dur.ac.uk"),
            (None, None),
            ("", None),
        ],
    )
    def test_canonicalise_domain(self, url, expected):
        assert canonicalise_domain(url) == expected

    def test_no_domain_value_carries_url_syntax(self):
        for url in (
            "http://www.uni-stuttgart.de/home/index.en.html",
            "https://investors.lockheedmartin.com/",
            "https://example.com/a/b?c=d#e",
        ):
            domain = canonicalise_domain(url)
            assert domain and not any(
                ch in domain for ch in ("/", "?", "#", ":")
            )
            assert not domain.startswith(("http", "www."))


class TestCanonicalDepartmentHost:
    """`department_domain` loses its path but KEEPS its subdomain."""

    @pytest.mark.parametrize(
        "url, expected",
        [
            (
                "https://medschool.umich.edu/departments/radiation-oncology",
                "medschool.umich.edu",
            ),
            ("https://be.mit.edu", "be.mit.edu"),
            ("https://chemistry.stanford.edu/", "chemistry.stanford.edu"),
            ("physics.stanford.edu", "physics.stanford.edu"),
            ("https://chem.yale.edu/people?page=2", "chem.yale.edu"),
            ("https://physics.yale.edu#top", "physics.yale.edu"),
            ("https://clas.ufl.edu/chemistry", "clas.ufl.edu"),
        ],
    )
    def test_department_host_keeps_subdomain(self, url, expected):
        assert canonicalise_host(url) == expected

    def test_department_host_is_not_collapsed_like_domain(self):
        url = "https://chemistry.stanford.edu/faculty"
        assert canonicalise_host(url) == "chemistry.stanford.edu"
        assert canonicalise_domain(url) == "stanford.edu"


class TestOwnershipGuard:
    def test_registry_provenance_is_sufficient(self):
        decision = _resolve(
            "http://www.uni-stuttgart.de/home/index.en.html",
            name1="University of Stuttgart",
            registry="ROR",
        )
        assert decision.domain == "uni-stuttgart.de"
        assert decision.website_url == "https://uni-stuttgart.de"
        assert decision.verified_by == "registry"

    def test_registry_domain_never_carries_a_deep_path(self):
        decision = _resolve(
            "https://www.mayoclinic.org/patient-visitor-guide/florida",
            name1="Mayo Clinic FLA",
            registry="ROR",
        )
        assert decision.domain == "mayoclinic.org"
        assert decision.website_url == "https://mayoclinic.org"

    def test_name_similarity_accepts_the_organisations_own_domain(self):
        decision = _resolve(
            "https://investors.lockheedmartin.com",
            name1="Lockheed Martin Corp",
        )
        assert decision.domain == "lockheedmartin.com"
        assert decision.verified_by == "name"

    def test_candidate_failing_all_four_conditions_yields_no_domain(self):
        """Row 50: 'Delta Analytical' is not Delta Air Lines."""
        decision = _resolve("https://www.delta.com", name1="Delta Analytical")
        assert decision.domain is None
        assert decision.website_url is None
        assert decision.rejected is True
        assert decision.candidate == "delta.com"

    @pytest.mark.parametrize(
        "name1, url",
        [
            ("Acme Biotech", "https://aumbiotech.com"),
            ("Coastal Diagnostics", "https://coastalmedicalimaging.com"),
            ("Novabio Therapeutics", "https://novabiomedical.com"),
            ("Cardinal Research GRP", "https://cardinalhealth.com"),
            ("Cardinal Instruments", "https://cardinalhealth.com"),
            ("Cardinal Instruments", "https://cardinalguitars.com"),
            ("Gulf Coast Labs", "https://gulfcoastscientific.com"),
            ("Coastal Analytical Svcs", "https://coastalanalyticalinstruments.com"),
            ("Horizon Instruments", "https://horizononline.com"),
            ("Redwood Labs", "https://toxaccess.redwoodtoxicology.com"),
            ("Apex Corp", "https://apexcos.com"),
        ],
    )
    def test_another_companys_website_is_never_attached(self, name1, url):
        decision = _resolve(url, name1=name1)
        assert decision.domain is None
        assert decision.rejected is True

    def test_both_cardinal_instruments_rows_end_with_no_domain(self):
        """Rows 43/44 read the same name; neither candidate is theirs."""
        for url in ("https://cardinalhealth.com", "https://cardinalguitars.com"):
            assert _resolve(url, name1="Cardinal Instruments").domain is None

    def test_guard_can_be_disabled_but_canonicalisation_still_applies(self):
        decision = resolve_domain(
            "https://www.delta.com/deep/path",
            DomainEvidence(name1="Delta Analytical"),
            threshold=THRESHOLD,
            guard_enabled=False,
        )
        assert decision.domain == "delta.com"
        assert decision.website_url == "https://delta.com"
        assert decision.verified_by == "unguarded"

    def test_no_candidate_is_not_a_rejection(self):
        decision = _resolve(None, name1="Delta Analytical")
        assert decision.domain is None
        assert decision.rejected is False


class TestEmailEvidence:
    def test_record_email_beats_the_search_result(self):
        """Row 34: the record already held better evidence than the SERP."""
        decision = _resolve(
            "https://meridianlabs.ai",
            name1="Meridian Labs",
            email="ORDERS@MERIDIANLABS.COM",
        )
        assert decision.domain == "meridianlabs.com"
        assert decision.website_url == "https://meridianlabs.com"
        assert decision.verified_by == "email"

    @pytest.mark.parametrize(
        "address",
        [
            "someone@gmail.com", "someone@googlemail.com", "someone@outlook.com",
            "someone@hotmail.com", "someone@live.com", "someone@yahoo.com",
            "someone@yahoo.co.uk", "someone@aol.com", "someone@icloud.com",
            "someone@gmx.de", "someone@web.de", "someone@t-online.de",
            "someone@protonmail.com",
        ],
    )
    def test_generic_provider_does_not_satisfy_condition_3(self, address):
        assert email_domain(address) is None
        # …and it cannot rescue a candidate that fails every other condition.
        decision = _resolve(
            "https://www.delta.com", name1="Delta Analytical", email=address,
        )
        assert decision.domain is None
        assert decision.rejected is True

    def test_generic_domain_classification(self):
        assert is_generic_email_domain("gmail.com") is True
        assert is_generic_email_domain("web.de") is True
        assert is_generic_email_domain("meridianlabs.com") is False

    def test_email_domain_ignores_subdomains_and_extra_addresses(self):
        assert email_domain("jane@chemistry.stanford.edu") == "stanford.edu"
        assert email_domain("jane@gmail.com; orders@acmelabs.com") == "acmelabs.com"


class TestOnDomainSerpEvidence:
    def test_page_title_naming_the_org_accepts_the_candidate(self):
        """An acronym host the name-similarity rule cannot reach on its own."""
        decision = _resolve(
            "https://admission.gatech.edu",
            name1="Georgia Institute of Technology",
            serp_url="https://admission.gatech.edu",
            serp_title="Georgia Institute of Technology — Undergraduate Admission",
        )
        assert name_similarity("Georgia Institute of Technology", "gatech.edu") < THRESHOLD
        assert decision.domain == "gatech.edu"
        assert decision.verified_by == "serp"

    def test_a_partial_token_overlap_is_not_evidence(self):
        """The SERP layer admits a result on one ≥4-char overlap; that is
        exactly how a stranger's 'Biotech' page slips through."""
        decision = _resolve(
            "https://aumbiotech.com",
            name1="Acme Biotech",
            serp_url="https://aumbiotech.com",
            serp_title="AUM Biotech — Home",
        )
        assert decision.domain is None

    def test_evidence_from_a_different_host_does_not_count(self):
        decision = _resolve(
            "https://www.delta.com",
            name1="Delta Analytical",
            serp_url="https://directory.example.com/delta-analytical",
            serp_title="Delta Analytical",
        )
        assert decision.domain is None


class TestFinaliseEmitsCanonicalFields:
    """The emitted values, end of pipeline — not just the helpers."""

    @staticmethod
    def _finalise(**overrides):
        import time

        from enrichment.orchestrator import finalise

        from tests.conftest import make_record

        fields = {
            "record_id": "X",
            "name1_enriched": "University of Michigan",
            "record_type": "research_institution",
            "flag_for_review": False,
            "flag_reason": None,
            "domain": "umich.edu",
            "website_url": "https://umich.edu",
        }
        fields.update(overrides)
        return finalise(make_record(**fields), time.monotonic())

    def test_department_domain_loses_its_path_but_keeps_its_subdomain(self):
        # The path goes; the subdomain — which is what names the unit — stays.
        out = self._finalise(
            name2_enriched="Department of Radiation Oncology",
            department_domain=(
                "https://rad-onc.medschool.umich.edu/patients/referrals"
            ),
        )
        assert out["department_domain"] == "https://rad-onc.medschool.umich.edu"

    def test_a_path_page_on_a_host_that_names_no_unit_is_dropped(self):
        # §5i. `canonicalise_host` drops the path, and "medschool" is the
        # medical school, not the Department of Radiation Oncology — the host
        # that would ship names the wrong unit. Before §5i this emitted
        # "https://medschool.umich.edu".
        out = self._finalise(
            name2_enriched="Department of Radiation Oncology",
            department_domain=(
                "https://medschool.umich.edu/departments/radiation-oncology"
            ),
        )
        assert out["department_domain"] is None

    @pytest.mark.parametrize(
        "host, name2",
        [
            ("be.mit.edu", "Department of Biological Engineering"),
            ("chemistry.stanford.edu", "Department of Chemistry"),
            ("physics.stanford.edu", "Department of Physics"),
            ("chem.yale.edu", "Department of Chemistry"),
            ("physics.yale.edu", "Department of Physics"),
        ],
    )
    def test_department_subdomains_are_untouched(self, host, name2):
        out = self._finalise(
            name2_enriched=name2, department_domain=host,
        )
        assert out["department_domain"] == f"https://{host}"

    def test_unverified_candidate_raises_the_flag_code(self):
        out = self._finalise(
            domain=None, website_url=None, _domain_unverified=True,
            ror_id="https://ror.org/00jmfr291",
        )
        # A bare `True` marker names no site, so there is nothing for
        # `_ship_unverified_domain` to put in the column — the code still
        # fires on the doubt itself.
        assert out["domain"] is None
        # ADVISORY: the code, the scope and the prose ship; the review request
        # does not. See `flags.ADVISORY_CODES`.
        assert out["flag_for_review"] is False
        assert out["flag_reason"]
        # Scoped to the one field in doubt: the registry match stands.
        assert out["flag_codes"] == ["domain-unverified"]
        assert out["flagged_fields"] == ["domain"]


class TestNameSimilarity:
    def test_hyphenated_label_is_split(self):
        assert name_similarity("Universität Stuttgart", "uni-stuttgart.de") > (
            name_similarity("Universität Stuttgart", "unistuttgart.de")
        )

    def test_threshold_separates_the_demo_pairs(self):
        """The tuning evidence behind DOMAIN_NAME_MATCH_THRESHOLD=82."""
        own = name_similarity("Lockheed Martin Corp", "lockheedmartin.com")
        other = name_similarity("Acme Biotech", "aumbiotech.com")
        assert other < THRESHOLD <= own

    def test_missing_inputs_score_zero(self):
        assert name_similarity(None, "example.com") == 0.0
        assert name_similarity("Example", None) == 0.0


class TestCountryGate:
    """A candidate whose ccTLD places it in another country is disqualified
    before the ownership conditions are consulted.

    The conditions cannot catch this on their own: name similarity is the one
    that would have accepted `unilever.be`, and it accepts it precisely BECAUSE
    the name is right. Only the country distinguishes the parent's Belgian site
    from a Texas subsidiary's record.
    """

    @staticmethod
    def _r(candidate, gate=True, **evidence):
        return resolve_domain(
            candidate,
            DomainEvidence(**evidence),
            threshold=THRESHOLD,
            guard_enabled=True,
            country_gate_enabled=gate,
        )

    def test_name_match_does_not_rescue_a_foreign_candidate(self):
        # Name similarity would carry this: "Unilever" against the label
        # "unilever" is an exact token match.
        d = self._r("https://www.unilever.be/", name1="Unilever", country="US")
        assert d.domain is None
        assert d.rejected is True
        assert d.rejected_by == "country"
        assert d.candidate == "unilever.be"

    def test_same_country_name_match_still_accepted(self):
        d = self._r("https://www.unilever.be/", name1="Unilever", country="BE")
        assert d.domain == "unilever.be"
        assert d.verified_by == "name"

    def test_gtld_unaffected(self):
        d = self._r("https://www.unilever.com/", name1="Unilever", country="US")
        assert d.domain == "unilever.com"

    def test_registry_provenance_is_exempt(self):
        # ROR/GLEIF STATING the website is a fact about the organisation, not a
        # guess about it — a US-registered subsidiary whose registry record
        # names a .de site is reporting something true.
        d = self._r(
            "https://www.basf.de/", name1="BASF Corporation",
            country="US", registry="ROR",
        )
        assert d.domain == "basf.de"
        assert d.verified_by == "registry"

    def test_stated_website_witness_is_exempt(self):
        d = self._r(
            "https://www.unilever.be/", name1="Unilever Trumbull Research",
            country="US",
            stated_websites=(("wikidata", "https://www.unilever.be/"),),
        )
        assert d.domain == "unilever.be"
        assert d.verified_by == "witness_wikidata"

    def test_email_condition_still_rescues_the_record(self):
        # The email path does not attribute the candidate — it DISCARDS it for
        # a domain the record already carries — so the disqualifier must not
        # take that rescue away.
        d = self._r(
            "https://www.unilever.be/", name1="Meridian Labs",
            country="US", email="orders@meridianlabs.com",
        )
        assert d.domain == "meridianlabs.com"
        assert d.verified_by == "email"

    def test_on_domain_serp_evidence_does_not_rescue(self):
        d = self._r(
            "https://www.unilever.be/about",
            name1="Unilever Trumbull Research Services",
            country="US",
            serp_url="https://www.unilever.be/about",
            serp_title="Unilever Trumbull Research Services",
        )
        assert d.domain is None
        assert d.rejected_by == "country"

    def test_page_identity_does_not_rescue(self):
        d = self._r(
            "https://www.unilever.be/", name1="Unilever",
            country="US", page_identity=True,
        )
        assert d.domain is None
        assert d.rejected_by == "country"

    def test_gate_off_restores_the_previous_answer(self):
        d = self._r(
            "https://www.unilever.be/", name1="Unilever",
            country="US", gate=False,
        )
        assert d.domain == "unilever.be"
        assert d.verified_by == "name"

    def test_no_country_on_the_record_fails_open(self):
        d = self._r("https://www.unilever.be/", name1="Unilever", country=None)
        assert d.domain == "unilever.be"

    def test_ordinary_rejection_still_reads_as_conditions(self):
        # The two rejection routes must stay distinguishable — one leaves a
        # domain worth a reviewer's time and the other does not.
        d = self._r("https://delta.com/", name1="Delta Analytical", country="US")
        assert d.rejected is True
        assert d.rejected_by == "conditions"


class TestCountryConflict:
    @pytest.mark.parametrize(
        "domain, country, expected",
        [
            ("unilever.be", "US", "BE"),        # the live case
            ("unilever.be", "BE", None),
            ("unilever.com", "US", None),       # gTLD — no country claim
            ("example.org", "DE", None),
            ("basf.de", "Germany", None),       # country name, not a code
            ("bbc.co.uk", "GB", None),          # .uk ↔ ISO GB
            ("bbc.co.uk", "United Kingdom", None),
            ("bbc.co.uk", "US", "GB"),
            ("example.ai", "US", None),         # worldwide ccTLD
            ("example.io", "DE", None),
            ("example.co", "US", None),
            ("example.eu", "BE", None),         # member state
            ("example.eu", "US", "EU"),
            ("example.de", "Ruritania", None),  # unreadable country → open
            ("example.de", None, None),
            (None, "US", None),
            ("example.pharmacy", "US", None),   # long gTLD
        ],
    )
    def test_conflicts(self, domain, country, expected):
        assert country_conflict(domain, country) == expected


class TestCountryRejectionRaisesNoReviewFlag:
    """`domain-unverified` means "a candidate was found and a human has to
    decide about it". A country rejection is a fact, not a doubt — there is
    nothing for a reviewer to decide, so no flag is raised and no domain is
    named in prose a reviewer could read as a suggestion."""

    @staticmethod
    def _write(candidate, **evidence):
        from enrichment.provenance import EnrichedRecord
        from utils.domain_resolver import write_domain

        rec = EnrichedRecord({})
        decision = write_domain(
            rec, candidate, DomainEvidence(**evidence),
            threshold=THRESHOLD, guard_enabled=True, country_gate_enabled=True,
        )
        return rec, decision

    def test_country_rejection_sets_no_unverified_marker(self):
        rec, decision = self._write(
            "https://www.unilever.be/", name1="Unilever", country="US",
        )
        assert decision.rejected_by == "country"
        assert rec.get("_domain_unverified") in (None, False)
        assert rec["domain_rejected"] is True   # still blocks later candidates

    def test_ordinary_rejection_still_names_the_candidate(self):
        rec, decision = self._write(
            "https://delta.com/", name1="Delta Analytical", country="US",
        )
        assert decision.rejected_by == "conditions"
        assert rec["_domain_unverified"] == "delta.com"
