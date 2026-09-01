# Change Task Template — A/B gate protocol

The template these pipeline change tasks are written against. Kept in the repo for the
same reason `docs/thesis-doc-prompt.md` is: the protocol is what makes two runs
comparable, and a protocol that lives only in a prompt drifts between tasks.

---

## The gate

Every change is gated by an A/B: the same batches, the same evidence cache, the code
being the only difference. A gate states an **allow-list** — the shape of change the task
expects — and anything outside it stops the task with a report rather than a fix.

    Gate: A/B. Allow-list: <the expected shape>. Print every such row.
    Anything else: stop.

### Cache state — frozen or warm

**A gate for a change that alters queries or prompts runs WARM on both sides.**

`run_batch.py --frozen` sets `CACHE_FROZEN=true`: a SERP, page fetch or LLM completion the
cache does not already hold is recorded as `evidence-unavailable-frozen` instead of being
issued. That is what makes a frozen run deterministic, and it is exactly wrong for a change
that alters what is asked:

* a new or edited prompt has a different cache key, so it misses **by construction**;
* a lane that misses degrades — `grounded_degraded`, `LLMUnavailableFrozen` — and the A/B
  then measures the degradation, not the change.

A gate for such a change is run warm on both sides: populate the cache with a non-frozen
run first, then run both sides frozen against that same populated cache, or run both sides
non-frozen. Either way the two sides must see the *same* cache contents — warming between
the two runs invalidates the comparison, because warming also fills pre-existing gaps that
the baseline was previously degrading on.

Changes that do **not** alter queries or prompts (a casing rule, a flag rule, a write gate
downstream of the model) gate frozen, which is cheaper and stricter.

**Every gate report states which it was.** One line, at the top of the result:

    Gate: frozen (misses 1 / 0 / 9 — identical both sides)
    Gate: warm  (cache populated <date/run>; 0 network calls both sides)

A report that does not say is not a gate result.

### Baseline hygiene

* The baseline is captured once, from unmodified code, and never edited.
* If the cache changes between baseline and candidate — for any reason, including warming
  for this task — the **baseline is re-taken** against the new cache before comparing.
  Prove it: a re-run of the baseline code must reproduce the baseline byte-for-byte, and
  the frozen-miss counts must match on both sides.
* A revert is verified the same way: re-running the reverted code must reproduce the last
  gated state exactly.

---

## The report

Per section, in this order:

1. **Gate line** — frozen or warm, with miss counts.
2. **A/B result** — identical, or the allow-listed rows, or stopped.
3. **Row count per file** — the number a reviewer will actually see.
4. **Every changed row printed**, with whatever the allow-list says to print alongside it
   (the refused proposal, the donor, the verdict, the evidence excerpt).
5. **`pytest -q`** — the baseline failure count, and confirmation that it is the same set.
6. **The diff.**

---

## Rules that apply to every task

* Additive where the task says additive. Named files only.
* `pytest -q` after every section; the baseline failures must be the same five, and the
  same five — a different set of five is a stop.
* A live end-to-end check (`/enrich` over the motivating records) is not a substitute for a
  gate, and a gate is not a substitute for it. The gate says what moved across the batch;
  the live check says whether the record is right. Assertions in a live check must be
  strong enough to fail: "not equal to the wrong answer" passes when nothing was written
  at all.
* `/enrich` returns SAP column names (`Name 1`, `Flag Codes`), not model field names, and
  does not expose `source`, `confidence` or `tier_used`. Assert on the provenance event
  instead.
