"""Reverse the order of every candidate list inside a recorded evidence cache.

The question this exists to answer is "is the pipeline reproducible, or does it
just replay a cache?" — and it is a fair question, because a diff of two runs
against one frozen cache cannot tell the two apart on its own.

This builds a second cache whose **content is identical and whose ORDER is
inverted**: the same SERP results, the same ROR ``items``, the same GLEIF
``data``, the same outgoing links — every list reversed. That is precisely the
perturbation a live API makes between two runs, and it is the perturbation that
made two runs of this batch disagree before Fix C. Nothing is added, removed or
edited.

Run the batch against both caches and diff the two runs:

* **identical output** ⇒ selection genuinely does not depend on the order the
  evidence arrived in. The cache is pinning *what* the sources said, not *which
  answer the pipeline gave*.
* **any difference** ⇒ something still breaks a tie on arrival order, and the
  zero-diff result was an artefact of replaying one recording.

It also tests Fix A(3) for free. The LLM cache is keyed on the rendered prompt,
so if reversing the evidence changes any prompt, that entry misses and the run
makes a live call — and ``evidence_network_calls_by_namespace["llm"]`` will say
so. Zero means no prompt depended on evidence order.

Usage::

    python tools/shuffle_evidence.py tests/fixtures _cache_shuffled
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

#: Namespaces copied verbatim. Their payloads carry no ordered candidate list
#: that any selection reads: a page read is one document, and a Wikidata
#: entity fetch is a mapping keyed by QID (the lane's own candidate order comes
#: from the search result, which IS reversed, under ``wikidata/``).
_VERBATIM = ("page_reads",)

#: ``(namespace, path-into-the-payload)`` for every recorded list that a
#: selection walks.
_LIST_PATHS: dict[str, tuple[tuple[str, ...], ...]] = {
    # `payload` is the SERP result list itself.
    "serp": ((),),
    # `payload.body` is the registry's JSON: ROR answers `items`, GLEIF `data`.
    "registry": (("body", "items"), ("body", "data")),
    # `payload.links` is the outgoing-link scrape the department probe ranks.
    "fetch": (("links",),),
    # `payload.search` is `wbsearchentities`' ordered hit list.
    "wikidata": (("search",),),
}


def _reverse_at(payload: Any, path: tuple[str, ...]) -> tuple[Any, bool]:
    """Reverse the list at *path* inside *payload*. Returns (payload, changed)."""
    if not path:
        return (list(reversed(payload)), True) if isinstance(payload, list) else (payload, False)
    if not isinstance(payload, dict):
        return payload, False
    head, rest = path[0], path[1:]
    if head not in payload:
        return payload, False
    inner, changed = _reverse_at(payload[head], rest)
    if changed:
        payload[head] = inner
    return payload, changed


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    reversed_files = 0
    copied = 0
    for namespace_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        name = namespace_dir.name
        out_dir = dst / name
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = _LIST_PATHS.get(name)
        for entry in sorted(namespace_dir.glob("*.json")):
            if paths is None or name in _VERBATIM:
                shutil.copy2(entry, out_dir / entry.name)
                copied += 1
                continue
            raw = json.loads(entry.read_text(encoding="utf-8"))
            payload = raw.get("payload")
            touched = False
            for path in paths:
                payload, changed = _reverse_at(payload, path)
                touched = touched or changed
            raw["payload"] = payload
            (out_dir / entry.name).write_text(
                json.dumps(raw, indent=1), encoding="utf-8",
            )
            reversed_files += touched
            copied += 1

    print(f"wrote {copied} entries to {dst}")
    print(f"candidate lists reversed in {reversed_files} of them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
