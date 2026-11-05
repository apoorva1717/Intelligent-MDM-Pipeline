"""Step AH item 5: would a rendering fetch (headless Chromium) recover the
pages our HTTP fetch could not read? Measurement only, no rule change.

Takes the pages that failed in the given usage JSONs (403 / 5xx / timeout,
or read but script-rendered: ``needs_js`` / empty) on the entity's own,
pointer or candidate hosts, for the entities whose Name 1 contains one of
``--names``, and renders each once in headless Chromium (Playwright, one
page at a time, ``--timeout`` seconds). TLS is verified as usual. Writes per
page: the HTTP fetch result, the rendered result (HTTP status, characters of
text), the VAT-ID-shaped strings on each, and the seconds.

    python tools/vat_render_probe.py --usage a_usage.json b_usage.json \\
        --names Bayer BMW VAMED --out logs/DE_VAT_AH_render_probe.xlsx
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

KINDS = ("own", "pointer", "candidate", "institution", "verify", "parent")


def failed_pages(usage_files: list[str], names: list[str]) -> list[dict]:
    """``{entity, url, kind, http}`` for every failed own-host page, once per URL."""
    seen, out = set(), []
    for f in usage_files:
        for u in json.loads(Path(f).read_text(encoding="utf-8")):
            name = u.get("name_1") or ""
            if not any(n.lower() in name.lower() for n in names):
                continue
            hosts = u.get("hosts") or {}
            for url, p in (u.get("pages") or {}).items():
                from vat.web_pages import host_of
                kind = p.get("kind") or (hosts.get(host_of(url)) or {}).get("kind")
                status = p.get("status")
                bad = p.get("blocked") or p.get("needs_js") or p.get("empty") or not (
                    isinstance(status, int) and status < 400) and str(status) != "200"
                if kind not in KINDS or not bad or url in seen:
                    continue
                seen.add(url)
                http = (f"blocked {p['blocked']}" if p.get("blocked") else
                        "script-only" if (p.get("needs_js") or p.get("empty")) else str(status))
                out.append({"entity": name, "url": url, "kind": kind, "http": http,
                            "http_chars": p.get("chars"), "http_hits": p.get("hits") or [],
                            "source": Path(f).stem})
    return out


def render(pages: list[dict], timeout_s: float) -> list[dict]:
    from playwright.sync_api import Error, sync_playwright

    from vat.entity_match import vat_candidates
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="de-DE")
        for p in pages:
            page = ctx.new_page()
            started = time.perf_counter()
            try:
                resp = page.goto(p["url"], timeout=timeout_s * 1000, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=max(1000, timeout_s * 1000
                                                                     - (time.perf_counter() - started) * 1000))
                except Error:
                    pass
                text = page.inner_text("body") if page.query_selector("body") else ""
                p.update(rendered_status=resp.status if resp else None, rendered_chars=len(text),
                         rendered_hits=vat_candidates(text), rendered_error=None)
            except Error as e:
                p.update(rendered_status=None, rendered_chars=0, rendered_hits=[],
                         rendered_error=str(e).splitlines()[0][:160])
            p["seconds"] = round(time.perf_counter() - started, 1)
            page.close()
            print(f"{p['entity'][:28]:28} {p['http']:16} -> {p['rendered_status']} {p['rendered_chars']:6} chars "
                  f"{p['rendered_hits']} {p['seconds']}s {p['url'][:80]} {p.get('rendered_error') or ''}", flush=True)
        browser.close()
    return pages


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--usage", nargs="+", required=True)
    ap.add_argument("--names", nargs="+", required=True)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    pages = render(failed_pages(args.usage, args.names), args.timeout)

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Pages"
    cols = ["entity", "url", "kind", "source", "http", "http_chars", "http_hits", "rendered_status",
            "rendered_chars", "rendered_hits", "rendered_error", "seconds"]
    ws.append(cols)
    for p in pages:
        ws.append([", ".join(v) if isinstance(v, list) else v for v in (p.get(c) for c in cols)])
    wb.save(args.out)
    print(f"Wrote {args.out}: {len(pages)} pages, "
          f"{sum(bool(p.get('rendered_chars')) and (p.get('rendered_status') or 0) < 400 for p in pages)} rendered, "
          f"{sum(bool(p.get('rendered_hits')) for p in pages)} with a VAT-ID-shaped string")
    return 0


if __name__ == "__main__":
    sys.exit(main())
