"""Watchdog: keeps the paper printing without anyone having to look after it.

Runs on its own schedule in GitHub Actions (watchdog.yml), separate from the printing workflow, so
anything that jams the printer can't jam the watchdog too. Every run it:
  1. re-enables the printing workflow if GitHub has switched it off (for example after 60 quiet days);
  2. cancels any printing run stuck queued, waiting or running for over 40 minutes (force-cancel
     if a normal cancel doesn't take). A stuck run like this caused the 30 Sep - 3 Oct outage;
  3. checks how old the live edition is, and if it's over 3 hours old, starts a fresh print;
  4. if the paper has been stale for over 8 hours, or the site is unreachable, opens a GitHub
     issue titled "Pole Position is not updating" (GitHub emails the repo owner), and closes it
     automatically once the paper is fresh again.

Stdlib + the `gh` CLI that comes preinstalled on GitHub's runners. Needs GH_TOKEN and REPO.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

REPO = os.environ["REPO"]
WORKFLOW = "daily.yml"
REPRINT_AFTER_H = 3
ALERT_AFTER_H = 8
STUCK_AFTER_MIN = 40
ISSUE_TITLE = "Pole Position is not updating"
NOW = datetime.now(timezone.utc)


def log(msg: str) -> None:
    print(f"[watchdog] {msg}", flush=True)


def gh(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def site_url() -> str:
    r = gh("api", f"repos/{REPO}/pages", "--jq", ".html_url")
    url = r.stdout.strip() if r.returncode == 0 else ""
    if not url:
        owner, name = REPO.split("/", 1)
        url = f"https://{owner.lower()}.github.io/{name}/"
    return url.rstrip("/") + "/"


def edition_age_hours(url: str) -> tuple[float | None, str]:
    """Age of the live edition in hours, or None if the site can't be read."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"{url}data.json?watchdog={int(time.time())}",
                                         headers={"User-Agent": "pole-position-watchdog", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                meta = json.loads(resp.read().decode("utf-8"))["meta"]
            gen = datetime.fromisoformat(meta["generated_at"])
            return (NOW - gen).total_seconds() / 3600, meta.get("edition_label", "")
        except Exception as e:  # noqa: BLE001
            log(f"could not read the live edition (attempt {attempt + 1}): {e}")
            time.sleep(10)
    return None, ""


def unjam_runs() -> int:
    """Cancel printing runs that have been stuck too long. Returns how many were cancelled."""
    r = gh("run", "list", "--workflow", WORKFLOW, "--limit", "30",
           "--json", "databaseId,status,createdAt,number")
    if r.returncode != 0:
        log(f"could not list runs: {r.stderr.strip()}")
        return 0
    cancelled = 0
    for run in json.loads(r.stdout or "[]"):
        if run["status"] not in ("queued", "waiting", "pending", "requested", "in_progress"):
            continue
        age_min = (NOW - datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))).total_seconds() / 60
        if age_min < STUCK_AFTER_MIN:
            continue
        rid = str(run["databaseId"])
        log(f"run #{run['number']} has been '{run['status']}' for {age_min:.0f} min, cancelling it")
        gh("run", "cancel", rid)
        time.sleep(15)
        st = gh("run", "view", rid, "--json", "status", "--jq", ".status").stdout.strip()
        if st != "completed":
            log(f"run #{run['number']} still '{st}', force-cancelling")
            gh("api", "-X", "POST", f"repos/{REPO}/actions/runs/{rid}/force-cancel")
        cancelled += 1
    return cancelled


def open_issue_number() -> str | None:
    r = gh("issue", "list", "--state", "open", "--search", f'"{ISSUE_TITLE}" in:title',
           "--json", "number,title")
    for it in json.loads(r.stdout or "[]") if r.returncode == 0 else []:
        if it.get("title") == ISSUE_TITLE:
            return str(it["number"])
    return None


def alert(body: str) -> None:
    num = open_issue_number()
    if num:
        gh("issue", "comment", num, "--body", body)
        log(f"updated alert issue #{num}")
    else:
        r = gh("issue", "create", "--title", ISSUE_TITLE, "--body", body)
        log(f"opened alert issue: {r.stdout.strip() or r.stderr.strip()}")


def all_clear(label: str) -> None:
    num = open_issue_number()
    if num:
        gh("issue", "close", num, "--comment", f"Fixed: the paper is printing again (latest edition: {label}). Closed automatically by the watchdog.")
        log(f"closed alert issue #{num}")


def main() -> None:
    gh("workflow", "enable", WORKFLOW)  # no-op when already enabled
    url = site_url()
    age, label = edition_age_hours(url)
    log(f"site {url}  edition age: {'unreachable' if age is None else f'{age:.1f} h'}  ({label})")

    cancelled = unjam_runs()

    if age is None or age > REPRINT_AFTER_H or cancelled:
        r = gh("workflow", "run", WORKFLOW)
        log("started a fresh print" if r.returncode == 0 else f"could not start a print: {r.stderr.strip()}")

    if age is None or age > ALERT_AFTER_H:
        state = "can't be reached" if age is None else f"is {age:.0f} hours old ({label})"
        alert(
            f"@{REPO.split('/')[0]} the live edition {state}. The watchdog has cancelled stuck runs and started a fresh print, "
            f"and will keep trying every 2 hours. This issue closes by itself once the paper is fresh.\n\n"
            f"- Site: {url}\n- Printing runs: https://github.com/{REPO}/actions/workflows/{WORKFLOW}\n"
            f"- Checked at {NOW.strftime('%Y-%m-%d %H:%M UTC')}"
        )
    elif age is not None and age <= REPRINT_AFTER_H:
        all_clear(label)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log(f"watchdog error: {e}")
        sys.exit(1)
