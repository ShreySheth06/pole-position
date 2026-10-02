"""Pre-publish gate: never put a broken page on the live site.

Run after build.py and before the deploy step. Exits 1 if the page is broken (missing, truncated,
no data, no stories, or the page script doesn't parse). The deploy is then skipped and readers keep
the last good edition, and the watchdog notices the paper going stale. Stdlib only. Node is used
for the script syntax check when it's available (it is on GitHub's runners).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def check(site_dir: str = "site") -> list[str]:
    site = Path(site_dir)
    errs: list[str] = []

    index = site / "index.html"
    html = index.read_text(encoding="utf-8") if index.exists() else ""
    if len(html) < 50_000:
        errs.append(f"index.html is missing or too small ({len(html)} bytes)")
    if "__EDITION_DATA__" in html:
        errs.append("edition data was not injected into index.html")
    if "</html>" not in html[-2000:].lower():
        errs.append("index.html looks truncated (no closing </html>)")

    # The data embedded in the page must parse, carry stories, and be from this build.
    m = re.search(r'<script id="edition-data" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        errs.append("embedded edition data block not found")
    else:
        try:
            d = json.loads(m.group(1).replace("<\\/", "</"))
            stories = sum(len(s.get("stories") or []) for s in d.get("sections") or [])
            if stories < 5:
                errs.append(f"only {stories} stories in the edition")
            gen = datetime.fromisoformat(d["meta"]["generated_at"])
            # build.py re-serves the last good edition (flagged stale, with a notice) when no news
            # could be fetched at all; that is a deliberate, honest page, so it may be older.
            if not d["meta"].get("stale") and datetime.now(timezone.utc) - gen > timedelta(hours=2):
                errs.append(f"edition timestamp {gen.isoformat()} is not from this build")
        except Exception as e:  # noqa: BLE001
            errs.append(f"embedded edition data is unreadable: {e}")

    # A JavaScript syntax error would leave readers with a blank page, so parse every inline script.
    node = shutil.which("node")
    if node and html:
        for i, js in enumerate(re.findall(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>(.*?)</script>", html, re.S)):
            if not js.strip():
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(js)
            r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
            Path(f.name).unlink(missing_ok=True)
            if r.returncode != 0:
                errs.append(f"inline script #{i} has a syntax error: {r.stderr.strip()[:300]}")
    return errs


if __name__ == "__main__":
    problems = check(sys.argv[1] if len(sys.argv) > 1 else "site")
    if problems:
        print("NOT PUBLISHING: the new edition failed its checks; the live site keeps the last good edition.")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("Page checks passed.")
