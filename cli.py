#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import argparse
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://nightly.cs.washington.edu/"
LOGS_URL = BASE_URL + "logs/"
USERNAME = "uwplse"
PASSWORD = "uwplse"
COMPLETE_RE = re.compile(r"^Nightly used memory=.*timeout=.*$", re.MULTILINE)


@dataclass(frozen=True)
class LogEntry:
    name: str
    url: str


@dataclass(frozen=True)
class RepoRun:
    date: str
    time: str
    branch: str


class NginxIndexParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.entries: list[LogEntry] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        self._href = dict(attrs).get("href")
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        href = self._href
        text = "".join(self._text).strip()
        self._href = None
        self._text = []
        if not href.endswith(".log") or text == "Parent directory/":
            return
        self.entries.append(LogEntry(Path(href).name, urllib.parse.urljoin(self.base_url, href)))


def make_opener() -> urllib.request.OpenerDirector:
    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, BASE_URL, USERNAME, PASSWORD)
    return urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(password_mgr))


def fetch_logs_index(opener: urllib.request.OpenerDirector) -> str:
    with opener.open(LOGS_URL) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_log_bytes(opener: urllib.request.OpenerDirector, url: str, start: int) -> tuple[str, int]:
    req = urllib.request.Request(url)
    req.add_header("Range", f"bytes={start}-")
    try:
        with opener.open(req) as response:
            data = response.read()
            if response.status == 206:
                return data.decode("utf-8", errors="replace"), start + len(data)
            if len(data) <= start:
                return "", start
            return data[start:].decode("utf-8", errors="replace"), len(data)
    except urllib.error.HTTPError as exc:
        if exc.code == 416:
            return "", start
        raise


def parse_html_entries(payload: str) -> list[LogEntry]:
    parser = NginxIndexParser(LOGS_URL)
    parser.feed(payload)
    deduped: dict[str, LogEntry] = {}
    for entry in parser.entries:
        deduped[entry.name] = entry
    return list(deduped.values())


def load_entries() -> list[LogEntry]:
    return parse_html_entries(fetch_logs_index(make_opener()))


def github_repo(url: str) -> str | None:
    if url.startswith("git@github.com:"):
        path = url.removeprefix("git@github.com:")
    elif url.startswith("https://github.com/"):
        path = url.removeprefix("https://github.com/")
    else:
        return None
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return path


def infer_repo(cwd: str) -> str:
    result = subprocess.run(
        ["git", "-C", cwd, "remote", "-v"],
        capture_output=True,
        text=True,
        check=True,
    )
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            repo = github_repo(parts[1])
            if repo is not None:
                return repo
    raise ValueError(f"could not infer GitHub repo from git remotes in {cwd}")


def strip_timestamp_prefix(stem: str) -> str | None:
    parts = stem.split("-")
    if len(parts) < 4:
        return None
    if len(parts[0]) != 4 or len(parts[1]) != 2 or len(parts[2]) != 2:
        return None
    if len(parts[3]) != 6 or not parts[3].isdigit():
        return None
    rest = parts[4:]
    if rest and rest[0].isdigit():
        rest = rest[1:]
    return "-".join(rest) if rest else ""


def repo_entries(entries: list[LogEntry], repo: str) -> list[LogEntry]:
    repo_name = repo.split("/")[-1]
    matched: list[LogEntry] = []
    for entry in entries:
        rest = strip_timestamp_prefix(Path(entry.name).stem)
        if rest and rest.startswith(repo_name + "-"):
            matched.append(entry)
    return matched


def recent_repo_entries(entries: list[LogEntry], repo: str) -> list[LogEntry]:
    matched = repo_entries(entries, repo)
    matched.sort(key=lambda entry: entry.name, reverse=True)
    return list(reversed(matched[:20]))


def parse_repo_run(repo: str, entry: LogEntry) -> RepoRun:
    parts = Path(entry.name).stem.split("-")
    repo_name = repo.split("/")[-1]
    branch_parts = parts[5 + len(repo_name.split("-")) :]
    raw_time = parts[3]
    return RepoRun(
        date="-".join(parts[:3]),
        time=":".join([raw_time[:2], raw_time[2:4], raw_time[4:6]]),
        branch="-".join(branch_parts),
    )


def normalize_time(value: str | None) -> str | None:
    if value is None:
        return None
    digits = value.replace(":", "")
    if len(digits) == 6 and digits.isdigit():
        return ":".join([digits[:2], digits[2:4], digits[4:6]])
    return value


def find_repo_log(
    entries: list[LogEntry],
    repo: str,
    branch: str,
    date: str | None = None,
    time_value: str | None = None,
) -> LogEntry | None:
    normalized_time = normalize_time(time_value)
    matched: list[LogEntry] = []
    for entry in repo_entries(entries, repo):
        run = parse_repo_run(repo, entry)
        if run.branch != branch:
            continue
        if date is not None and run.date != date:
            continue
        if normalized_time is not None and run.time != normalized_time:
            continue
        matched.append(entry)
    if not matched:
        return None
    matched.sort(key=lambda entry: entry.name)
    return matched[-1]


def log_complete(text: str) -> bool:
    return COMPLETE_RE.search(text) is not None


def print_log(opener: urllib.request.OpenerDirector, url: str) -> None:
    with opener.open(url) as response:
        sys.stdout.write(response.read().decode("utf-8", errors="replace"))


def tail_log(opener: urllib.request.OpenerDirector, url: str) -> None:
    offset = 0
    recent = ""
    while True:
        chunk, offset = fetch_log_bytes(opener, url, offset)
        if chunk:
            sys.stdout.write(chunk)
            sys.stdout.flush()
            recent = (recent + chunk)[-4096:]
            if log_complete(recent):
                return
        time.sleep(1)


def cmd_list(repo: str) -> int:
    try:
        entries = recent_repo_entries(load_entries(), repo)
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {LOGS_URL}: {exc}", file=sys.stderr)
        return 1
    if not entries:
        print(f"No runs found for repo {repo.split('/')[-1]}.", file=sys.stderr)
        return 1
    for entry in entries:
        run = parse_repo_run(repo, entry)
        print(f"{run.date:10} {run.time:8} {run.branch}")
    return 0


def cmd_log(repo: str, branch: str, date: str | None, time_value: str | None, follow: bool) -> int:
    opener = make_opener()
    try:
        entry = find_repo_log(load_entries(), repo, branch, date, time_value)
        if entry is None:
            print("No matching log found.", file=sys.stderr)
            return 1
        if follow:
            tail_log(opener, entry.url)
        else:
            print_log(opener, entry.url)
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {LOGS_URL}: {exc}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query nightly.cs.washington.edu logs.")
    parser.add_argument("-C", dest="cwd", default=".", help="Change to this directory first.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List the latest runs for a repo.")
    list_parser.add_argument("--repo", help="Repository name, such as herbie or owner/herbie.")

    log_parser = subparsers.add_parser("log", help="Print a log for a repo branch.")
    log_parser.add_argument("--repo", help="Repository name, such as herbie or owner/herbie.")
    log_parser.add_argument("-f", action="store_true", dest="follow", help="Follow the log until it completes.")
    log_parser.add_argument("branch", help="Branch name.")
    log_parser.add_argument("date", nargs="?", default=None, help="Run date as YYYY-MM-DD.")
    log_parser.add_argument("time", nargs="?", default=None, help="Run time as HH:MM:SS or HHMMSS.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    os.chdir(args.cwd)
    try:
        repo = args.repo or infer_repo(".")
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.command == "list":
        return cmd_list(repo)
    if args.command == "log":
        return cmd_log(repo, args.branch, args.date, args.time, args.follow)
    raise AssertionError(f"unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
