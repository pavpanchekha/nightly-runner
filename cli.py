#!/usr/bin/env python3

from __future__ import annotations

import codecs
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import argparse
import datetime
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://nightly.cs.washington.edu/"
LOGS_URL = BASE_URL + "logs/"
LOGS_SORTED_URL = LOGS_URL + "?C=M&O=D"
REPORTS_URL = BASE_URL + "reports/"
USERNAME = "uwplse"
PASSWORD = "uwplse"
CURL_PARALLEL_MAX = 32
COMPLETE_RE = re.compile(r"^Nightly used memory=.*timeout=.*$", re.MULTILINE)
PUBLISH_RE = re.compile(r"^Publishing report directory .* to .*/reports/([^/]+)/([^/\n]+)$", re.MULTILINE)


@dataclass(frozen=True)
class LogEntry:
    name: str
    url: str


@dataclass(frozen=True)
class RepoRun:
    date: str
    time: str
    branch: str


@dataclass(frozen=True)
class RunSelector:
    branch: str | None
    date: str | None
    time: str | None


@dataclass(frozen=True)
class DownloadStats:
    file_count: int
    curl_seconds: float
    ungzip_seconds: float


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

    def drain_entries(self) -> list[LogEntry]:
        entries = self.entries
        self.entries = []
        return entries


def make_opener() -> urllib.request.OpenerDirector:
    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, BASE_URL, USERNAME, PASSWORD)
    return urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(password_mgr))


def fetch_logs_index(opener: urllib.request.OpenerDirector) -> str:
    with opener.open(LOGS_URL) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_text(opener: urllib.request.OpenerDirector, url: str) -> str:
    with opener.open(url) as response:
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


def iter_html_entries(response: urllib.response.addinfourl, base_url: str) -> Iterator[LogEntry]:
    parser = NginxIndexParser(base_url)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        chunk = response.read(65536)
        if not chunk:
            break
        parser.feed(decoder.decode(chunk))
        yield from parser.drain_entries()
    tail = decoder.decode(b"", final=True)
    if tail:
        parser.feed(tail)
    parser.close()
    yield from parser.drain_entries()


def iter_entries(opener: urllib.request.OpenerDirector, *, newest_first: bool) -> Iterator[LogEntry]:
    url = LOGS_SORTED_URL if newest_first else LOGS_URL
    with opener.open(url) as response:
        yield from iter_html_entries(response, url)


def load_entries() -> list[LogEntry]:
    return list(iter_entries(make_opener(), newest_first=False))


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


def repo_entries(entries: Iterable[LogEntry], repo: str) -> Iterator[LogEntry]:
    repo_name = repo.split("/")[-1]
    for entry in entries:
        rest = strip_timestamp_prefix(Path(entry.name).stem)
        if rest and rest.startswith(repo_name + "-"):
            yield entry


def recent_repo_entries(entries: Iterable[LogEntry], repo: str) -> list[LogEntry]:
    matched: list[LogEntry] = []
    for entry in repo_entries(entries, repo):
        matched.append(entry)
        if len(matched) >= 20:
            break
    return list(reversed(matched))


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


def matching_repo_entries(
    entries: Iterable[LogEntry],
    repo: str,
    selector: RunSelector,
) -> list[LogEntry]:
    normalized_time = normalize_time(selector.time)
    matched: list[LogEntry] = []
    for entry in repo_entries(entries, repo):
        run = parse_repo_run(repo, entry)
        if selector.branch is not None and run.branch != selector.branch:
            continue
        if selector.date is not None and run.date != selector.date:
            continue
        if normalized_time is not None and run.time != normalized_time:
            continue
        matched.append(entry)
    return matched


def latest_matching_repo_entry(
    entries: Iterable[LogEntry],
    repo: str,
    selector: RunSelector,
) -> LogEntry | None:
    for entry in matching_repo_entries(entries, repo, selector):
        return entry
    return None


def timing_line(step: str, seconds: float) -> str:
    return f"download timing: {step:<18} {seconds:.3f}s"


def log_complete(text: str) -> bool:
    return COMPLETE_RE.search(text) is not None


def print_log(opener: urllib.request.OpenerDirector, url: str) -> None:
    sys.stdout.write(fetch_text(opener, url))


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


def find_report_url_in_log(repo: str, log_text: str) -> str | None:
    repo_name = repo.split("/")[-1]
    matched: str | None = None
    for match in PUBLISH_RE.finditer(log_text):
        if match.group(1) != repo_name:
            continue
        matched = REPORTS_URL + repo_name + "/" + match.group(2)
    return matched


def fetch_manifest(opener: urllib.request.OpenerDirector, report_url: str) -> dict[str, object]:
    manifest = json.loads(fetch_text(opener, report_url + "/nightly_info.json"))
    if not isinstance(manifest, dict):
        raise ValueError("nightly_info.json did not contain a JSON object")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("nightly_info.json did not contain a files list")
    return manifest


def format_manifest_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.utcoffset() == datetime.timedelta(0):
        return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")
    return parsed.isoformat(sep=" ")


def manifest_text(manifest: dict[str, object]) -> str:
    repo = manifest.get("repo")
    branch = manifest.get("branch")
    lines: list[str] = []
    if isinstance(repo, str) and isinstance(branch, str):
        lines.append(f"{repo} / {branch}")
        lines.append("")

    details: list[tuple[str, object]] = []
    if "status" in manifest:
        details.append(("Status", manifest["status"]))
    commit = manifest.get("commit_short", manifest.get("commit"))
    if commit is not None:
        details.append(("Commit", commit))
    started = format_manifest_time(manifest.get("started_at"))
    if started is not None:
        details.append(("Started", started))
    finished = format_manifest_time(manifest.get("finished_at"))
    if finished is not None:
        details.append(("Finished", finished))
    duration = manifest.get("duration_human", manifest.get("duration_seconds"))
    if duration is not None:
        details.append(("Duration", duration))
    files = manifest["files"]
    assert isinstance(files, list)
    details.append(("Files", len(files)))
    if "report_url" in manifest:
        details.append(("Report", manifest["report_url"]))
    if "log_url" in manifest:
        details.append(("Log", manifest["log_url"]))
    image_url = manifest.get("image_url")
    if image_url:
        details.append(("Image", image_url))

    for label, value in details:
        lines.append(f"{label:8} {value}")
    return "\n".join(lines)


def manifest_paths(path_value: object, gzip_value: object) -> tuple[str, str]:
    if not isinstance(path_value, str):
        raise ValueError("manifest file path was not a string")
    if not isinstance(gzip_value, bool):
        raise ValueError("manifest gzip flag was not a boolean")
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe manifest path {path_value!r}")
    if gzip_value:
        if path_value.endswith(".gz"):
            return path_value, path_value[:-3]
        return path_value + ".gz", path_value
    return path_value, path_value


def download_report_files(
    report_url: str,
    files: list[object],
    output_dir: Path,
) -> DownloadStats:
    output_dir.mkdir(parents=True, exist_ok=True)

    file_paths: list[tuple[str, str]] = []
    for file_info in files:
        if not isinstance(file_info, dict):
            raise ValueError("manifest file entry was not an object")
        file_paths.append(manifest_paths(file_info.get("path"), file_info.get("gzip")))

    curl_start = time.perf_counter()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as config_file:
        for remote_relpath, _ in file_paths:
            remote_url = report_url + "/" + urllib.parse.quote(remote_relpath)
            local_path = output_dir / remote_relpath
            print(f'url = "{remote_url}"', file=config_file)
            print(f'output = "{local_path}"', file=config_file)
        config_file.flush()
        subprocess.run(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--create-dirs",
                "--parallel",
                "--parallel-max",
                str(min(CURL_PARALLEL_MAX, max(1, len(file_paths)))),
                "--user",
                f"{USERNAME}:{PASSWORD}",
                "--config",
                config_file.name,
            ],
            check=True,
        )
    curl_seconds = time.perf_counter() - curl_start

    ungzip_start = time.perf_counter()
    for remote_relpath, local_relpath in file_paths:
        if remote_relpath == local_relpath:
            continue
        remote_path = output_dir / remote_relpath
        local_path = output_dir / local_relpath
        with gzip.open(remote_path, "rb") as f_in, local_path.open("wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        remote_path.unlink()
    ungzip_seconds = time.perf_counter() - ungzip_start
    return DownloadStats(len(file_paths), curl_seconds, ungzip_seconds)


def fetch_selected_manifest(
    opener: urllib.request.OpenerDirector,
    repo: str,
    selector: RunSelector,
) -> dict[str, object] | None:
    entry = latest_matching_repo_entry(iter_entries(opener, newest_first=True), repo, selector)
    if entry is None:
        return None
    log_text = fetch_text(opener, entry.url)
    report_url = find_report_url_in_log(repo, log_text)
    if report_url is None:
        raise ValueError("No published report found in log.")
    return fetch_manifest(opener, report_url)


def cmd_download(repo: str, selector: RunSelector) -> int:
    assert selector.branch is not None
    opener = make_opener()
    total_start = time.perf_counter()
    try:
        entries_start = time.perf_counter()
        entries = iter_entries(opener, newest_first=True)
        entry = latest_matching_repo_entry(entries, repo, selector)
        entries_seconds = time.perf_counter() - entries_start
        print(timing_line("load/select run", entries_seconds), file=sys.stderr)
        if entry is None:
            print("No matching log found.", file=sys.stderr)
            return 1

        log_fetch_start = time.perf_counter()
        log_text = fetch_text(opener, entry.url)
        log_fetch_seconds = time.perf_counter() - log_fetch_start
        print(timing_line("fetch log", log_fetch_seconds), file=sys.stderr)

        report_url_start = time.perf_counter()
        report_url = find_report_url_in_log(repo, log_text)
        report_url_seconds = time.perf_counter() - report_url_start
        print(timing_line("parse report url", report_url_seconds), file=sys.stderr)
        if report_url is None:
            print("No published report found in log.", file=sys.stderr)
            return 1

        manifest_start = time.perf_counter()
        manifest = fetch_manifest(opener, report_url)
        manifest_seconds = time.perf_counter() - manifest_start
        print(timing_line("fetch manifest", manifest_seconds), file=sys.stderr)
        files = manifest["files"]
        assert isinstance(files, list)
        output_dir = Path(urllib.parse.urlsplit(report_url).path.rstrip("/")).name
        stats = download_report_files(report_url, files, Path(output_dir))
        print(timing_line("curl download", stats.curl_seconds), file=sys.stderr)
        print(timing_line("ungzip files", stats.ungzip_seconds), file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {BASE_URL}: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, gzip.BadGzipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    total_seconds = time.perf_counter() - total_start
    print(timing_line("total", total_seconds), file=sys.stderr)
    print(f"Downloaded {stats.file_count} files to {output_dir}/")
    return 0


def cmd_list(
    repo: str,
    selector: RunSelector,
) -> int:
    opener = make_opener()
    try:
        if selector.branch is None and selector.date is None and selector.time is None:
            entries = recent_repo_entries(iter_entries(opener, newest_first=True), repo)
        else:
            entries = list(reversed(matching_repo_entries(iter_entries(opener, newest_first=True), repo, selector)))
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


def cmd_log(repo: str, selector: RunSelector, follow: bool) -> int:
    assert selector.branch is not None
    opener = make_opener()
    try:
        entry = latest_matching_repo_entry(iter_entries(opener, newest_first=True), repo, selector)
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


def cmd_status(repo: str, selector: RunSelector) -> int:
    assert selector.branch is not None
    opener = make_opener()
    try:
        manifest = fetch_selected_manifest(opener, repo, selector)
        if manifest is None:
            print("No matching log found.", file=sys.stderr)
            return 1
        print(manifest_text(manifest))
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {LOGS_URL}: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def add_run_selector_args(parser: argparse.ArgumentParser, *, branch_required: bool) -> None:
    branch_nargs = None if branch_required else "?"
    parser.add_argument("branch", nargs=branch_nargs, default=None, help="Branch name.")
    parser.add_argument("date", nargs="?", default=None, help="Run date as YYYY-MM-DD.")
    parser.add_argument("time", nargs="?", default=None, help="Run time as HH:MM:SS or HHMMSS.")


def selector_from_args(args: argparse.Namespace) -> RunSelector:
    return RunSelector(args.branch, args.date, args.time)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query nightly.cs.washington.edu logs and reports.")
    parser.add_argument("-C", dest="cwd", default=".", help="Change to this directory first.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List runs for a repo.")
    list_parser.add_argument("--repo", help="Repository name, such as herbie or owner/herbie.")
    add_run_selector_args(list_parser, branch_required=False)

    log_parser = subparsers.add_parser("log", help="Print a log for a repo branch.")
    log_parser.add_argument("--repo", help="Repository name, such as herbie or owner/herbie.")
    log_parser.add_argument("-f", action="store_true", dest="follow", help="Follow the log until it completes.")
    add_run_selector_args(log_parser, branch_required=True)

    status_parser = subparsers.add_parser("status", help="Show published report status for a repo branch run.")
    status_parser.add_argument("--repo", help="Repository name, such as herbie or owner/herbie.")
    add_run_selector_args(status_parser, branch_required=True)

    download_parser = subparsers.add_parser("download", help="Download a published report for a repo branch run.")
    download_parser.add_argument("--repo", help="Repository name, such as herbie or owner/herbie.")
    add_run_selector_args(download_parser, branch_required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    os.chdir(args.cwd)
    selector = selector_from_args(args)
    try:
        repo = args.repo or infer_repo(".")
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.command == "list":
        return cmd_list(repo, selector)
    if args.command == "log":
        return cmd_log(repo, selector, args.follow)
    if args.command == "status":
        return cmd_status(repo, selector)
    if args.command == "download":
        return cmd_download(repo, selector)
    raise AssertionError(f"unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
