#!/usr/bin/env python3

from __future__ import annotations

import codecs
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import argparse
import datetime
import getpass
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

CURL_PARALLEL_MAX = 32
COMPLETE_RE = re.compile(r"^Nightly used memory=.*timeout=.*$", re.MULTILINE)
PUBLISH_RE = re.compile(r"^Publishing report directory .* to .*/reports/([^/]+)/([^/\n]+)$", re.MULTILINE)
SETUP_COMMAND = "cli setup <url>"
STATE_FILENAME = "state.json"


@dataclass(frozen=True)
class ClientConfig:
    base_url: str
    username: str
    password: str

    @property
    def logs_url(self) -> str:
        return urllib.parse.urljoin(self.base_url, "logs/")

    @property
    def logs_sorted_url(self) -> str:
        return self.logs_url + "?C=M&O=D"

    @property
    def reports_url(self) -> str:
        return urllib.parse.urljoin(self.base_url, "reports/")

    @property
    def index_url(self) -> str:
        return self.base_url

    @property
    def sync_url(self) -> str:
        return urllib.parse.urljoin(self.base_url, "dryrun")

    @property
    def start_url(self) -> str:
        return urllib.parse.urljoin(self.base_url, "runnow")

    @property
    def curl_auth(self) -> str:
        return f"{self.username}:{self.password}"


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
class StartTarget:
    repo: str
    branch: str
    disabled: bool


@dataclass(frozen=True)
class IndexState:
    sync_disabled: bool
    start_targets: list[StartTarget]


@dataclass(frozen=True)
class DownloadStats:
    file_count: int
    curl_seconds: float
    ungzip_seconds: float


def short_repo_name(repo_name: str) -> str:
    return repo_name.split("/")[-1]


class MissingClientConfig(ValueError):
    pass


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


def client_state_dir() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "nightlies"
        return Path.home() / "AppData" / "Roaming" / "nightlies"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "nightlies"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "nightlies"
    return Path.home() / ".local" / "share" / "nightlies"


def client_state_path() -> Path:
    return client_state_dir() / STATE_FILENAME


def setup_hint() -> str:
    return f"Run `{SETUP_COMMAND}`."


def normalize_base_url(url: str) -> str:
    normalized = url.strip()
    parsed = urllib.parse.urlsplit(normalized)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("nightly URL must be an http or https URL")
    if not normalized.endswith("/"):
        normalized += "/"
    return normalized


def load_client_config() -> ClientConfig:
    path = client_state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MissingClientConfig(f"client is not configured. {setup_hint()}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid client config {path}: {exc}. {setup_hint()}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid client config {path}: expected a JSON object. {setup_hint()}")

    nightly_url = payload.get("nightly_url")
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(nightly_url, str) or not isinstance(username, str) or not isinstance(password, str):
        raise ValueError(
            f"invalid client config {path}: expected string fields nightly_url, username, and password. {setup_hint()}"
        )
    return ClientConfig(normalize_base_url(nightly_url), username, password)


def save_client_config(client_config: ClientConfig) -> Path:
    path = client_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            json.dump(
                {
                    "nightly_url": client_config.base_url,
                    "username": client_config.username,
                    "password": client_config.password,
                },
                handle,
                indent=2,
            )
            handle.write("\n")
            temp_path = handle.name
        if os.name != "nt":
            os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
        return path
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


class IndexParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.sync_disabled = False
        self.start_targets: list[StartTarget] = []
        self._form_action: str | None = None
        self._form_inputs: dict[str, str] = {}
        self._button_disabled = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "form":
            action = attr_map.get("action")
            self._form_action = urllib.parse.urljoin(self.base_url, action) if action else None
            self._form_inputs = {}
            self._button_disabled = False
            return
        if self._form_action is None:
            return
        if tag == "input":
            name = attr_map.get("name")
            value = attr_map.get("value")
            if name is not None and value is not None:
                self._form_inputs[name] = value
            return
        if tag == "button":
            self._button_disabled = "disabled" in attr_map

    def handle_endtag(self, tag: str) -> None:
        if tag != "form" or self._form_action is None:
            return
        form_path = urllib.parse.urlsplit(self._form_action).path.rstrip("/")
        if form_path == urllib.parse.urlsplit(urllib.parse.urljoin(self.base_url, "dryrun")).path.rstrip("/"):
            self.sync_disabled = self._button_disabled
        elif form_path == urllib.parse.urlsplit(urllib.parse.urljoin(self.base_url, "runnow")).path.rstrip("/"):
            repo = self._form_inputs.get("repo")
            branch = self._form_inputs.get("branch")
            if repo is not None and branch is not None:
                self.start_targets.append(StartTarget(repo, branch, self._button_disabled))
        self._form_action = None
        self._form_inputs = {}
        self._button_disabled = False


def make_opener(client_config: ClientConfig) -> urllib.request.OpenerDirector:
    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, client_config.base_url, client_config.username, client_config.password)
    return urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(password_mgr))


def fetch_logs_index(opener: urllib.request.OpenerDirector, client_config: ClientConfig) -> str:
    with opener.open(client_config.logs_url) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_index(opener: urllib.request.OpenerDirector, client_config: ClientConfig) -> str:
    with opener.open(client_config.index_url) as response:
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


def parse_html_entries(payload: str, client_config: ClientConfig) -> list[LogEntry]:
    parser = NginxIndexParser(client_config.logs_url)
    parser.feed(payload)
    deduped: dict[str, LogEntry] = {}
    for entry in parser.entries:
        deduped[entry.name] = entry
    return list(deduped.values())


def parse_index_state(payload: str, client_config: ClientConfig) -> IndexState:
    parser = IndexParser(client_config.base_url)
    parser.feed(payload)
    parser.close()
    return IndexState(parser.sync_disabled, parser.start_targets)


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


def iter_entries(
    opener: urllib.request.OpenerDirector,
    client_config: ClientConfig,
    *,
    newest_first: bool,
) -> Iterator[LogEntry]:
    url = client_config.logs_sorted_url if newest_first else client_config.logs_url
    with opener.open(url) as response:
        yield from iter_html_entries(response, url)


def load_entries(client_config: ClientConfig) -> list[LogEntry]:
    return list(iter_entries(make_opener(client_config), client_config, newest_first=False))


def fetch_index_state(opener: urllib.request.OpenerDirector, client_config: ClientConfig) -> IndexState:
    return parse_index_state(fetch_index(opener, client_config), client_config)


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


def escape_branch_filename(branch: str) -> str:
    return branch.replace("%", "_25").replace("/", "_2f")


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
    repo_name = short_repo_name(repo)
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
    repo_name = short_repo_name(repo)
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


def resolve_start_target(
    index_state: IndexState,
    client_config: ClientConfig,
    repo: str,
    branch: str,
) -> StartTarget:
    exact_matches = [
        target
        for target in index_state.start_targets
        if target.branch == branch and target.repo == repo
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        raise ValueError(f"multiple configured start targets matched repo {repo!r} and branch {branch!r}")

    repo_name = short_repo_name(repo)
    short_matches = [
        target
        for target in index_state.start_targets
        if target.branch == branch and short_repo_name(target.repo) == repo_name
    ]
    if len(short_matches) == 1:
        return short_matches[0]
    if len(short_matches) > 1:
        options = ", ".join(sorted({target.repo for target in short_matches}))
        raise ValueError(
            f"branch {branch!r} exists in multiple configured repos matching {repo!r}: {options}"
        )

    configured_repos = {target.repo for target in index_state.start_targets if target.repo == repo}
    configured_repos.update(
        target.repo for target in index_state.start_targets if short_repo_name(target.repo) == repo_name
    )
    if configured_repos:
        raise ValueError(f"branch {branch!r} is not available for repo {repo!r}")
    raise ValueError(f"repo {repo!r} is not configured on {client_config.base_url}")


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


def find_report_url_in_log(client_config: ClientConfig, repo: str, log_text: str) -> str | None:
    repo_name = short_repo_name(repo)
    matched: str | None = None
    for match in PUBLISH_RE.finditer(log_text):
        if match.group(1) != repo_name:
            continue
        matched = client_config.reports_url + repo_name + "/" + match.group(2)
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
    client_config: ClientConfig,
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
                client_config.curl_auth,
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
    client_config: ClientConfig,
    repo: str,
    selector: RunSelector,
) -> dict[str, object] | None:
    entry = latest_matching_repo_entry(iter_entries(opener, client_config, newest_first=True), repo, selector)
    if entry is None:
        return None
    log_text = fetch_text(opener, entry.url)
    report_url = find_report_url_in_log(client_config, repo, log_text)
    if report_url is None:
        raise ValueError("No published report found in log.")
    return fetch_manifest(opener, report_url)


def cmd_setup(url: str) -> int:
    try:
        username = input("Username: ").strip()
        if not username:
            raise ValueError("username must not be empty")
        password = getpass.getpass("Password: ")
        if not password:
            raise ValueError("password must not be empty")
        client_config = ClientConfig(normalize_base_url(url), username, password)
        path = save_client_config(client_config)
    except (EOFError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Saved CLI config to {path}")
    return 0


def post_form(
    opener: urllib.request.OpenerDirector,
    url: str,
    fields: dict[str, str],
) -> None:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with opener.open(request) as response:
        response.read()


def format_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        payload = exc.read().decode("utf-8", errors="replace")
    except OSError:
        payload = ""
    finally:
        exc.close()
    text = " ".join(re.sub(r"<[^>]+>", " ", payload).split())
    if "Nightly sync already running" in text:
        return "Nightly sync already running"
    queued_match = re.search(r"Job nightly:[^ ]+ already queued", text)
    if queued_match is not None:
        return queued_match.group(0)
    if text:
        return f"HTTP {exc.code}: {text}"
    return f"HTTP {exc.code}: {exc.reason}"


def cmd_sync(client_config: ClientConfig) -> int:
    opener = make_opener(client_config)
    try:
        index_state = fetch_index_state(opener, client_config)
        if index_state.sync_disabled:
            print("error: Nightly sync already running", file=sys.stderr)
            return 1
        post_form(opener, client_config.sync_url, {})
    except urllib.error.HTTPError as exc:
        print(f"error: {format_http_error(exc)}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {client_config.base_url}: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_start(client_config: ClientConfig, repo: str, branch: str) -> int:
    opener = make_opener(client_config)
    try:
        index_state = fetch_index_state(opener, client_config)
        target = resolve_start_target(index_state, client_config, repo, branch)
        if index_state.sync_disabled:
            print("error: Nightly sync already running", file=sys.stderr)
            return 1
        if target.disabled:
            branch_filename = escape_branch_filename(target.branch)
            print(
                f"error: Job nightly:{target.repo}:{branch_filename} already queued",
                file=sys.stderr,
            )
            return 1
        post_form(opener, client_config.start_url, {"repo": target.repo, "branch": target.branch})
    except urllib.error.HTTPError as exc:
        print(f"error: {format_http_error(exc)}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {client_config.base_url}: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_download(client_config: ClientConfig, repo: str, selector: RunSelector) -> int:
    assert selector.branch is not None
    opener = make_opener(client_config)
    total_start = time.perf_counter()
    try:
        entries_start = time.perf_counter()
        entries = iter_entries(opener, client_config, newest_first=True)
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
        report_url = find_report_url_in_log(client_config, repo, log_text)
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
        stats = download_report_files(report_url, files, Path(output_dir), client_config)
        print(timing_line("curl download", stats.curl_seconds), file=sys.stderr)
        print(timing_line("ungzip files", stats.ungzip_seconds), file=sys.stderr)
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {client_config.base_url}: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, gzip.BadGzipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    total_seconds = time.perf_counter() - total_start
    print(timing_line("total", total_seconds), file=sys.stderr)
    print(f"Downloaded {stats.file_count} files to {output_dir}/")
    return 0


def cmd_list(
    client_config: ClientConfig,
    repo: str,
    selector: RunSelector,
) -> int:
    opener = make_opener(client_config)
    try:
        if selector.branch is None and selector.date is None and selector.time is None:
            entries = recent_repo_entries(iter_entries(opener, client_config, newest_first=True), repo)
        else:
            entries = list(
                reversed(matching_repo_entries(iter_entries(opener, client_config, newest_first=True), repo, selector))
            )
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {client_config.logs_url}: {exc}", file=sys.stderr)
        return 1
    if not entries:
        print(f"No runs found for repo {short_repo_name(repo)}.", file=sys.stderr)
        return 1
    for entry in entries:
        run = parse_repo_run(repo, entry)
        print(f"{run.date:10} {run.time:8} {run.branch}")
    return 0


def cmd_log(client_config: ClientConfig, repo: str, selector: RunSelector, follow: bool) -> int:
    assert selector.branch is not None
    opener = make_opener(client_config)
    try:
        entry = latest_matching_repo_entry(iter_entries(opener, client_config, newest_first=True), repo, selector)
        if entry is None:
            print("No matching log found.", file=sys.stderr)
            return 1
        if follow:
            tail_log(opener, entry.url)
        else:
            print_log(opener, entry.url)
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {client_config.logs_url}: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_status(client_config: ClientConfig, repo: str, selector: RunSelector) -> int:
    assert selector.branch is not None
    opener = make_opener(client_config)
    try:
        manifest = fetch_selected_manifest(opener, client_config, repo, selector)
        if manifest is None:
            print("No matching log found.", file=sys.stderr)
            return 1
        print(manifest_text(manifest))
    except urllib.error.URLError as exc:
        print(f"error: failed to fetch {client_config.logs_url}: {exc}", file=sys.stderr)
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

    setup_parser = subparsers.add_parser("setup", help="Save the nightly URL and credentials.")
    setup_parser.add_argument("url", help="Nightly base URL, such as https://nightly.cs.washington.edu/.")

    subparsers.add_parser("sync", help="Start a sync-with-GitHub dry run from the web UI.")

    list_parser = subparsers.add_parser("list", help="List runs for a repo.")
    list_parser.add_argument("--repo", help="Repository name, such as herbie or owner/herbie.")
    add_run_selector_args(list_parser, branch_required=False)

    start_parser = subparsers.add_parser("start", help="Start a single repo branch run from the web UI.")
    start_parser.add_argument("--repo", help="Repository name, such as herbie or owner/herbie.")
    start_parser.add_argument("branch", help="Branch name.")

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
    if args.command == "setup":
        return cmd_setup(args.url)
    try:
        client_config = load_client_config()
    except (MissingClientConfig, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.command == "sync":
        return cmd_sync(client_config)
    if args.command == "start":
        try:
            repo = args.repo or infer_repo(".")
        except (subprocess.CalledProcessError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return cmd_start(client_config, repo, args.branch)
    selector = selector_from_args(args)
    try:
        repo = args.repo or infer_repo(".")
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.command == "list":
        return cmd_list(client_config, repo, selector)
    if args.command == "log":
        return cmd_log(client_config, repo, selector, args.follow)
    if args.command == "status":
        return cmd_status(client_config, repo, selector)
    if args.command == "download":
        return cmd_download(client_config, repo, selector)
    raise AssertionError(f"unknown command {args.command}")


if __name__ == "__main__":
    sys.exit(main())
