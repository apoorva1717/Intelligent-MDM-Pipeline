"""The cache state a gate was taken against.

Two numbers, so a later reader can tell whether two gate lines are comparable:
the number of recorded entries, and a hash over the sorted key list. The hash
is over KEYS only, never contents — a re-recorded answer for the same key does
not change what the gate could replay.
"""
from __future__ import annotations
import hashlib, os, sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "logs/cache")
if not root.is_dir():
    print(f"{root}: no such cache directory"); raise SystemExit(1)
keys = sorted(
    str(p.relative_to(root))
    for p in root.rglob("*")
    if p.is_file() and not p.name.startswith(".")
)
digest = hashlib.sha256("\n".join(keys).encode()).hexdigest()[:12]
by_ns: dict[str, int] = {}
for k in keys:
    by_ns[k.split(os.sep)[0] if os.sep in k else "(root)"] = (
        by_ns.get(k.split(os.sep)[0] if os.sep in k else "(root)", 0) + 1
    )
print(f"cache {root}")
print(f"  entries: {len(keys)}   keys-sha256[:12]: {digest}")
print("  by namespace: " + ", ".join(f"{n}={c}" for n, c in sorted(by_ns.items())))
