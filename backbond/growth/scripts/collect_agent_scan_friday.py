#!/usr/bin/env python3
"""Collect public Agent Scan Friday metrics; never instruments the package."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def request_json(url: str, token: str | None = None):
    headers = {"Accept": "application/vnd.github+json" if "api.github.com" in url else "application/json", "User-Agent": "backbond-agent-scan-scoreboard/1.2"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
        return json.load(response)


def npm_metrics(package: str, end: dt.date) -> dict:
    start = end - dt.timedelta(days=6)
    quoted = urllib.parse.quote(package, safe="@")
    try:
        latest = request_json(f"https://registry.npmjs.org/{quoted}/latest")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return {
            "package": package,
            "latest": None,
            "downloads_by_day": None,
            "downloads_last_7_days": None,
            "gap": f"npm package {package} is not yet published.",
            "per_version_gap": "npm's public downloads API does not expose per-version counts.",
        }
    daily = request_json(f"https://api.npmjs.org/downloads/range/{start}:{end}/{quoted}")
    weekly = request_json(f"https://api.npmjs.org/downloads/point/{start}:{end}/{quoted}")
    return {"package": package, "latest": latest.get("version"), "downloads_by_day": {x["day"]: x["downloads"] for x in daily.get("downloads", [])}, "downloads_last_7_days": weekly.get("downloads"), "gap": None, "per_version_gap": "npm's public downloads API does not expose per-version counts."}


def github_metrics(repo: str, token: str | None, fp_label: str) -> dict:
    base = f"https://api.github.com/repos/{repo}"
    info = request_json(base, token)
    issues = request_json(f"{base}/issues?state=open&labels={urllib.parse.quote(fp_label)}&per_page=100", token)
    fps = [issue for issue in issues if "pull_request" not in issue]
    response_hours = []
    for issue in fps:
        comments = request_json(issue["comments_url"] + "?per_page=100", token)
        if comments:
            opened = dt.datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
            first = dt.datetime.fromisoformat(comments[0]["created_at"].replace("Z", "+00:00"))
            response_hours.append(round((first - opened).total_seconds() / 3600, 2))
    try:
        referrers = request_json(f"{base}/traffic/popular/referrers", token)
        referrer_count = len(referrers)
        referrer_gap = None
    except urllib.error.HTTPError as exc:
        referrer_count = None
        referrer_gap = f"GitHub referrers unavailable ({exc.code}); push access is required."
    return {"stars": info.get("stargazers_count"), "forks": info.get("forks_count"), "watchers": info.get("subscribers_count"), "referrers": referrer_count, "referrers_gap": referrer_gap, "open_false_positive_issues": len(fps), "median_first_response_hours": statistics.median(response_hours) if response_hours else None}


def page_version(url: str, package: str) -> str | None:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "backbond-agent-scan-scoreboard/1.2"}), timeout=20) as response:
        html = response.read().decode("utf-8", "replace")
    for pattern in (r'data-agent-scan-version=["\']v?([^"\']+)', rf'{re.escape(package)}@([0-9][\w.-]*)'):
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1)
    return None


def cdn_count(log_path: str | None, demo_path: str | None) -> tuple[int | None, str | None]:
    if not log_path or not demo_path:
        return None, "Set AGENT_SCAN_CDN_LOG and AGENT_SCAN_DEMO_PATH to the owned CDN log and exact demo path."
    return sum(demo_path in line for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()), None


def display(value) -> str:
    return "—" if value is None else str(value)


def append_row(path: Path, result: dict) -> None:
    g, n, p, c = result["github"], result["npm"], result["page"], result["cdn"]
    date = result["effective_date"]
    text = path.read_text(encoding="utf-8")
    if re.search(rf"^\| {re.escape(date)} \|", text, re.M):
        raise ValueError(f"scoreboard already contains a row for {date}")
    row = "| {date} | {downloads} | {stars} | {forks} | {watchers} | {referrers} | {parity} | {fetches} | {fps} | {response} |\n".format(date=date, downloads=display(n["downloads_last_7_days"]), stars=display(g["stars"]), forks=display(g["forks"]), watchers=display(g["watchers"]), referrers=display(g["referrers"]), parity=f'{display(p["live_version"])}/{display(n["latest"])}', fetches=display(c["demo_file_fetch_count"]), fps=display(g["open_false_positive_issues"]), response=display(g["median_first_response_hours"]))
    marker = "<!-- friday-rows-end -->"
    if marker not in text:
        raise ValueError(f"scoreboard is missing {marker}")
    path.write_text(text.replace(marker, row + marker, 1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=dt.date.fromisoformat, default=dt.datetime.now(dt.timezone.utc).date())
    parser.add_argument("--append", type=Path)
    args = parser.parse_args()
    required = ("AGENT_SCAN_NPM_PACKAGE", "AGENT_SCAN_GITHUB_REPO", "AGENT_SCAN_PAGE_URL")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        parser.error("missing environment variables: " + ", ".join(missing))
    fetches, fetch_gap = cdn_count(os.getenv("AGENT_SCAN_CDN_LOG"), os.getenv("AGENT_SCAN_DEMO_PATH"))
    npm = npm_metrics(os.environ["AGENT_SCAN_NPM_PACKAGE"], args.date)
    live = page_version(os.environ["AGENT_SCAN_PAGE_URL"], os.environ["AGENT_SCAN_NPM_PACKAGE"])
    result = {"schema": "backbond.agent-scan.friday.v1.2", "observed_at": dt.datetime.now(dt.timezone.utc).isoformat(), "effective_date": args.date.isoformat(), "npm": npm, "github": github_metrics(os.environ["AGENT_SCAN_GITHUB_REPO"], os.getenv("GITHUB_TOKEN"), os.getenv("AGENT_SCAN_FP_LABEL", "false-positive")), "page": {"url": os.environ["AGENT_SCAN_PAGE_URL"], "live_version": live, "matches_latest": live == npm["latest"] if live else None}, "cdn": {"demo_file_fetch_count": fetches, "gap": fetch_gap}}
    if args.append:
        append_row(args.append, result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
