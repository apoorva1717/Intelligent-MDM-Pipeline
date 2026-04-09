"""Mock page fetcher for testing — returns curated page text.

Covers: person found with dept+group, dept only, person not on page, fetch timeout.
"""

from __future__ import annotations

import logging

from search.page_fetcher import PageFetcher

logger = logging.getLogger(__name__)

_MOCK_PAGES: dict[str, str] = {
    "chemistry.mit.edu/profile/jane-smith": (
        "Department of Chemistry Massachusetts Institute of Technology "
        "Dr. Jane Smith Professor of Chemistry NMR Spectroscopy Group "
        "Research interests include nuclear magnetic resonance spectroscopy "
        "and structural biology. Jane Smith joined the Department of Chemistry "
        "in 2015. She leads the NMR Spectroscopy Group within the department. "
        "Office: Building 18, Room 301 Phone: 617-253-1000 "
        "Email: jsmith@mit.edu"
    ),
    "chemistry.ucla.edu/faculty/john-doe": (
        "UCLA Department of Chemistry and Biochemistry "
        "John Doe Professor Research: Organic Chemistry and Synthesis "
        "Dr. John Doe is a professor in the Department of Chemistry and "
        "Biochemistry at UCLA. His research focuses on total synthesis of "
        "natural products. Organic Chemistry Research Group. "
        "Office: Young Hall 3077 Phone: 310-825-1234"
    ),
    "chemistry.stanford.edu/people/alice-johnson": (
        "Stanford University Department of Chemistry "
        "Alice Johnson Assistant Professor "
        "Dr. Alice Johnson studies analytical chemistry with a focus on "
        "mass spectrometry. She is a member of the Analytical Chemistry Group "
        "in the Department of Chemistry at Stanford University."
    ),
    "hopkinsmedicine.org/profiles/details/robert-chen": (
        "Johns Hopkins School of Medicine Department of Radiology "
        "Robert Chen, MD Professor of Radiology "
        "Dr. Robert Chen is a professor in the Department of Radiology "
        "at Johns Hopkins School of Medicine. His research focuses on "
        "MRI spectroscopy and neuroimaging. He directs the MRI Research Group."
    ),
    "pfizer.com/science/research/analytical-sciences": (
        "Pfizer Analytical Sciences Division "
        "Our Analytical Sciences team supports drug development through "
        "advanced characterization techniques including NMR, mass spectrometry, "
        "and chromatography. Based in Groton, Connecticut."
    ),
    "example-university.edu/people/faculty/profile": (
        "Department of Sciences Example University "
        "Faculty Profile Professor of Analytical Chemistry "
        "Research interests in spectroscopy and chemical analysis. "
        "Laboratory for Advanced Spectroscopy."
    ),
    "amris.ufl.edu": (
        "Advanced Magnetic Resonance Imaging and Spectroscopy (AMRIS) "
        "University of Florida. The AMRIS NMR Facility houses state-of-the-art "
        "NMR spectrometers. The Biomolecular NMR Lab provides services for "
        "structural biology, metabolomics and protein characterization. "
        "NMR Facility Director: Dr. Arthur Edison. Located at McKnight Brain "
        "Institute, University of Florida, Gainesville, FL 32611."
    ),
    "chem.ufl.edu": (
        "Department of Chemistry University of Florida. "
        "Research areas include analytical chemistry, organic chemistry, "
        "physical chemistry, and biochemistry. The NMR Facility is housed "
        "in the AMRIS building. Biomolecular NMR Lab researchers study "
        "protein structures using advanced spectroscopy techniques."
    ),
}


class MockPageFetcher(PageFetcher):
    """Mock page fetcher returning curated text based on URL fragments."""

    def __init__(self) -> None:
        super().__init__(timeout=10, max_chars=3000)

    async def fetch_page_text(self, url: str) -> str | None:
        """Return mock page text matched by URL substring."""
        url_lower = url.lower()

        # Simulate timeout for specific test URLs
        if "timeout" in url_lower or "error" in url_lower:
            logger.info("[MOCK] Page fetch: simulated timeout for %s", url[:80])
            return None

        for fragment, text in _MOCK_PAGES.items():
            if fragment in url_lower:
                logger.info("[MOCK] Page fetch: matched '%s' (%d chars)", fragment, len(text))
                return text

        logger.info("[MOCK] Page fetch: no mock for %s — returning None", url[:80])
        return None
