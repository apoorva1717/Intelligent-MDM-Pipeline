"""Tier 2 — LLM-only canonicalization of a user-supplied department name.

Runs when name2 is present but Tier 1 ROR child matching did not find
a match in the institution's ROR children list. Uses the LLM's existing
knowledge of well-known institutions to return the canonical form the
institution itself uses on its own website. Zero SerpAPI calls.

Confidence no longer decides whether the answer is used (§1a). The subject
the record supplied must survive (`subject_preserved`) and the answer must
not be a prefix downgrade; those are identity questions and they are the only
refusals. What the model reported travels on as provenance and decides how
selectively the record is flagged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from llm.openai_client import OpenAIClient
from llm.prompts import (
    TIER2_CANONICAL_SYSTEM_PROMPT,
    TIER2_CANONICAL_USER_PROMPT_TEMPLATE,
)
from utils.text_utils import _token_covers, expand_abbreviations

logger = logging.getLogger(__name__)

# A leading academic-unit prefix ("Department of", "Division of", …). Used to
# detect a DOWNGRADE — a canonical that is just the input with the unit prefix
# stripped ("Department of Biology" → "Biology"). The canonical direction is
# bare → "Department of X", never the reverse.
_UNIT_PREFIX_RE = re.compile(
    r"^(?:the\s+)?(?:Department|Dept\.?|Division|Div\.?|School|Faculty|"
    r"Institute|Center|Centre|College|Office)\s+of\s+",
    re.IGNORECASE,
)


def _is_prefix_downgrade(original: str, cleaned: str) -> bool:
    """True when *cleaned* is *original* with only a leading unit prefix removed
    ("Department of Biology" → "Biology")."""
    m = _UNIT_PREFIX_RE.match(original.strip())
    if not m:
        return False
    remainder = original.strip()[m.end():].strip()
    return bool(remainder) and remainder.lower() == cleaned.strip().lower()


# Unit words and connectors carry no subject identity, so they are dropped
# before the two sides are compared: the question is whether the model kept
# the SUBJECT the record supplied, not how it spelled the unit around it.
_UNIT_WORDS = {
    "department", "dept", "division", "div", "school", "faculty", "institute",
    "center", "centre", "college", "office", "laboratory", "lab",
}
_CONNECTOR_WORDS = {"the", "of", "and", "for", "in", "at", "on", "&"}


def _subject_tokens(text: str) -> list[str]:
    """The subject-bearing tokens of *text*, in order, lower-cased."""
    expanded = expand_abbreviations(text) or text
    return [
        w.lower() for w in re.findall(r"[A-Za-z0-9&]+", expanded)
        if w.lower() not in _UNIT_WORDS and w.lower() not in _CONNECTOR_WORDS
    ]


def _is_bare_code(token: str, original: str) -> bool:
    """True when *token* reads as a bare code rather than a subject word — a
    2-6 letter all-caps run sitting in an otherwise mixed-case value.

    This is the building / room / faculty code SAP records routinely staple
    onto a department ("Marine Biology, OCSB", where OCSB is the Ocean and
    Coastal Studies Building). An ALL-CAPS value is excluded outright: there
    every token is upper-case, so the signal says nothing.
    """
    if original.isupper():
        return False
    letters = re.sub(r"[^A-Za-z]", "", token)
    return 2 <= len(letters) <= 6 and letters.isupper()


def _is_pure_recanonicalisation(original: str, cleaned: str) -> bool:
    """True when *cleaned* is *original* re-worded into canonical unit form
    and nothing else — no subject word added, none dropped except bare codes.

    This is what makes a MEDIUM-confidence answer safe to keep. The model is
    routinely only medium on the *exact institutional wording* while being
    plainly right about the subject ("Marine Biology, OCSB" → "Department of
    Marine Biology": medium, because it cannot confirm TAMUG's exact unit
    name). Verifying that the answer adds no new content turns that into a
    deterministic reformatting the pipeline can stand behind, while every
    answer that swaps or invents a subject ("Office of Purchasing" →
    "Procurement Services", "& Health Sciences" → "School of Arts and
    Sciences") still fails the check and falls through to passthrough.
    """
    m = _UNIT_PREFIX_RE.match(cleaned.strip())
    if not m:
        # Not in "<Unit> of <Subject>" form — a suffix form ("Fire Department")
        # or a bare phrase is not a canonicalisation the pipeline asked for,
        # and `canonicalise_unit_name` handles those deterministically anyway.
        return False
    subject = _subject_tokens(cleaned.strip()[m.end():])
    if not subject:
        return False
    return _kept_subject_tokens(original) == subject


def _kept_subject_tokens(text: str) -> list[str]:
    """Subject tokens of *text*, in order, with bare building/room codes
    dropped.

    Differs from :func:`_subject_tokens` in one way that matters: the
    bare-code test reads the CASE-PRESERVED token ("OCSB"), so it has to run
    before the lower-casing rather than after it. Extracted so the Tier 2
    medium-confidence check and :func:`subject_preserved` ask the question
    with one implementation instead of two.
    """
    return [
        w.lower() for w in re.findall(r"[A-Za-z0-9&]+", expand_abbreviations(text) or text)
        if w.lower() not in _UNIT_WORDS
        and w.lower() not in _CONNECTOR_WORDS
        and not _is_bare_code(w, text)
    ]


def subject_preserved(original: str | None, suggestion: str | None) -> bool:
    """True when *suggestion* still names the unit *original* named.

    The department-slot counterpart of
    :func:`utils.text_utils.canonical_preserves_identity`, which is tuned for
    COMPANY names and rejects every legitimate department rewrite (its
    addable vocabulary carries "University" and "Institute", not "Department"
    or "Division", so even "Engineering" → "Department of Engineering" fails
    it).

    The question here is narrower and is the one Tier 2 already asks: did the
    model keep the SUBJECT the record supplied, whatever it did to the unit
    wording around it. Unit words and connectors carry no subject identity and
    are dropped from both sides; a bare building/room code the record stapled
    on ("Marine Biology, OCSB") is droppable too, exactly as Tier 2 allows.
    So re-wordings pass —

        "Engineering"          → "Department of Engineering"
        "Biochem"              → "Department of Biochemistry"
        "Marine Biology, OCSB" → "Department of Marine Biology"

    — and subject SWAPS fail, which is the whole point:

        "Edwards Air Force Base" → "412th Test Wing"
        "Office of Purchasing"   → "Procurement Services"

    Conservative in the same direction as its Name 1 counterpart: an original
    with no subject tokens at all (pure unit words) has no identity to
    preserve, so it returns True rather than blocking a reformat.
    """
    orig = _kept_subject_tokens((original or "").strip())
    if not orig:
        return True
    sugg = _subject_tokens((suggestion or "").strip())
    if not sugg:
        return False
    return all(any(_token_covers(o, s) for s in sugg) for o in orig)


@dataclass
class Tier2CanonicalResult:
    success: bool = False
    name2_enriched: str | None = None
    confidence: str = "none"
    reasoning: str = ""
    #: A refused proposal, kept so the flag can name it rather than leaving
    #: the reviewer to rediscover what the pipeline already knew.
    proposed_name: str | None = None


async def run_tier2_canonical(
    record_id: str,
    institution: str,
    name2: str,
    llm_client: OpenAIClient,
    authoritative: bool = True,
) -> Tier2CanonicalResult:
    """Single LLM call. No page fetch, no SERP."""
    result = Tier2CanonicalResult()

    if not institution or not name2:
        return result

    user_prompt = TIER2_CANONICAL_USER_PROMPT_TEMPLATE.format(
        institution=institution,
        name2=name2,
    )

    try:
        extraction = await llm_client.extract_json(
            TIER2_CANONICAL_SYSTEM_PROMPT, user_prompt,
        )
    except Exception as exc:
        logger.info("[%s] Tier 2 canonical: LLM call failed: %s", record_id, exc)
        return result

    official_name = extraction.get("official_name")
    confidence = (extraction.get("confidence") or "low").lower()
    reasoning = extraction.get("reasoning", "")

    if not official_name or not str(official_name).strip():
        logger.info("[%s] Tier 2 canonical: LLM returned no name", record_id)
        return result

    # Guard against literal "null" / "none" strings
    cleaned = str(official_name).strip()
    if cleaned.lower() in {"null", "none", "n/a", "na"}:
        return result

    # Trust a high-confidence answer outright — this tier is authoritative
    # for well-known institutions, not a best-effort guess. A medium answer
    # is kept only when it is a verified pure re-wording of the input, which
    # is not a guess either: no subject word was added or dropped.
    verified_recanonicalisation = (
        confidence == "medium" and _is_pure_recanonicalisation(name2, cleaned)
    )
    # Confidence is not a write gate (§1a). The restriction to high answers,
    # or medium ones that were provably pure re-wordings, discarded correct
    # unit names for exactly the records whose wording the model could not
    # certify — which is most abbreviated SAP department text. Identity
    # decides the write: `subject_preserved` below, and the gate at the write
    # point. What the model reported is recorded and drives the flag.
    if not authoritative and confidence != "high" and not verified_recanonicalisation:
        logger.info(
            "[%s] Tier 2 canonical: rejecting '%s' (confidence=%s, legacy)",
            record_id, cleaned, confidence,
        )
        return result

    # The subject check is NOT repeated here. `subject_preserved` is applied
    # once, at the write point, by `enrichment.name_gate` — §2's single gate.
    # Running it in the tier as well would put two implementations of "is this
    # still the same unit" on the same value, free to disagree, and would
    # discard the proposal before the gate could put it in the flag detail.

    # Guard against a DOWNGRADE: never accept a canonical that is just the input
    # with a unit prefix stripped ("Department of Biology" → "Biology"). The
    # canonical direction is bare → "Department of X", never the reverse.
    if _is_prefix_downgrade(name2, cleaned):
        logger.info(
            "[%s] Tier 2 canonical: rejecting downgrade '%s' → '%s'",
            record_id, name2, cleaned,
        )
        return result

    result.success = True
    result.name2_enriched = cleaned
    result.confidence = confidence
    result.reasoning = reasoning

    logger.info(
        "[%s] Tier 2 canonical: '%s' → '%s' (%s)",
        record_id, name2, cleaned,
        "verified re-canonicalisation, medium confidence"
        if verified_recanonicalisation else "high confidence",
    )
    return result
