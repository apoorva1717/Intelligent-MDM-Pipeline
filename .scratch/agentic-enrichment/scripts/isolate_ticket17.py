"""Attribute the before/after delta to a cause, instead of to the last commit.

The raw diff mixes at least three things:
  (a) the .env repair + ticket 20's cache purge -- SerpAPI actually answers now,
      and 251 poisoned empties no longer replay,
  (b) ticket 17's legal-form classifier source,
  (c) (b) applied to names that (a) improved.

(b) is separable because `classify()` is a pure function of the record's
evidence. Replaying the BEFORE run's own evidence through the CURRENT classifier
gives ticket 17's effect with retrieval held fixed.
"""
import json

from enrichment.classifier import TypeEvidence, classify

before = json.load(open(".scratch/agentic-enrichment/tmp/run100b.json", encoding="utf-8"))
after = json.load(open("logs/compare/after.json", encoding="utf-8"))

B = before["results"]
A = after["results"]

# ---- ticket 17 alone: BEFORE's names and BEFORE's registry evidence --------
# A registry-decided record is untouched by ticket 17 (it ranks below both), so
# replay only what the registries left undecided, exactly as the classifier
# would see it.
changed = 0
now_company = 0
for r in B:
    prov = (r.get("record_type_provenance") or "")
    if prov.startswith(("ror:", "gleif:")):
        continue
    was = r.get("record_type") or "unknown"
    verdict, source = classify(TypeEvidence(name1=r.get("name1_enriched")))
    if verdict != was:
        changed += 1
        if source == "legal_form":
            now_company += 1

print("== ticket 17 in isolation ==")
print("   BEFORE's own names, BEFORE's own registry evidence, CURRENT classifier")
print(f"   record_type changed on {changed} of 100   (legal_form decided {now_company})")

b_known = sum(1 for r in B if (r.get("record_type") or "unknown") != "unknown")
a_known = sum(1 for r in A if (r.get("record_type") or "unknown") != "unknown")
print(f"   record_type known: {b_known} -> {b_known + changed} attributable to 17")
print(f"   record_type known observed end-to-end: {b_known} -> {a_known}")
print(f"   => the remaining {a_known - b_known - changed} comes from better NAMES,")
print(f"      i.e. from retrieval, not from the classifier\n")

# ---- what retrieval did ----------------------------------------------------
print("== retrieval (.env repair + ticket 20 cache purge) ==")
for key in ("domain_from_registry", "domain_from_serp", "domain_from_page",
            "domain_from_witness", "domain_from_email",
            "page_reads_attempted", "page_corroborated",
            "evidence_network_calls", "tier3_count"):
    b, a = before["summary"].get(key), after["summary"].get(key)
    print(f"   {key:28s} {b} -> {a}")

b_dom = sum(1 for r in B if (r.get("domain") or "").strip())
a_dom = sum(1 for r in A if (r.get("domain") or "").strip())
print(f"   domain populated             {b_dom} -> {a_dom}")

# ---- did anything get WORSE? ----------------------------------------------
print("\n== regressions to look for ==")
b_ror = {(r.get("name1_original") or "", r.get("ror_id") or "") for r in B}
lost_ror = sum(
    1 for rb, ra in zip(B, A)
    if (rb.get("ror_id") or "") and not (ra.get("ror_id") or "")
)
lost_lei = sum(
    1 for rb, ra in zip(B, A)
    if (rb.get("lei_id") or "") and not (ra.get("lei_id") or "")
)
lost_dom = sum(
    1 for rb, ra in zip(B, A)
    if (rb.get("domain") or "") and not (ra.get("domain") or "")
)
print(f"   ROR id lost:    {lost_ror}")
print(f"   LEI id lost:    {lost_lei}")
print(f"   domain lost:    {lost_dom}")

# A record_type that went from a decided value to unknown is a real loss.
regressed = [
    (rb.get("name1_enriched"), rb.get("record_type"), ra.get("record_type"))
    for rb, ra in zip(B, A)
    if (rb.get("record_type") or "unknown") != "unknown"
    and (ra.get("record_type") or "unknown") == "unknown"
]
print(f"   record_type de-decided: {len(regressed)}")
for n, b, a in regressed[:10]:
    print(f"      {n} {b} -> {a}")

# research_institution -> company: right or wrong? Show them all, they are few.
flips = [
    (rb.get("name1_enriched"), ra.get("name1_enriched"),
     rb.get("record_type"), ra.get("record_type"),
     ra.get("record_type_provenance"))
    for rb, ra in zip(B, A)
    if rb.get("record_type") == "research_institution"
    and ra.get("record_type") == "company"
]
print(f"\n   research_institution -> company: {len(flips)}")
for bn, an, bt, at, prov in flips:
    print(f"      {str(bn)[:38]:38s} -> {str(an)[:38]:38s} [{prov}]")
