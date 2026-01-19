#!/usr/bin/env python3

from typing import Any, Dict, List, Sequence
from datetime import datetime
from pathlib import Path
import gzip, json, shlex, shutil, subprocess, sys, time
import config, slack

def log(msg: str) -> None:
    print(msg, flush=True)

def format_time(ts: float) -> str:
    t = float(ts)
    if t < 120:
        return f"{t:.1f}s"
    elif t < 120*60:
        return f"{t/60:.1f}m"
    else:
        return f"{t/60/60:.1f}h"

def format_cmd(s: Sequence[str | Path]) -> str:
    return shlex.join([str(part) for part in s])

def run(cmd: Sequence[str | Path], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
    log(f"Executing {format_cmd(cmd)}")
    return subprocess.run(cmd, **kwargs)

def parse_time(to: str | None) -> float | None:
    if to is None:
        return to
    units = {"hr": 3600, "h": 3600, "min": 60, "m": 60, "sec": 1, "s": 1}
    for unit, multiplier in units.items():
        if to.endswith(unit):
            return float(to[:-len(unit)]) * multiplier
    return float(to)

def format_size(size: int) -> str:
    units = ["KB", "MB", "GB", "TB", "PB"]
    s = float(size) / 1024
    for unit in units:
        if s < 1024:
            break
        s /= 1024
    return f"{s:.2f}{unit}"

def tree_size(root: Path) -> tuple[int, Path | None, int]:
    total = 0
    biggest = None
    biggest_size = 0
    for dirpath, _, files in root.walk():
        for name in files:
            path = dirpath / name
            size = path.stat().st_size
            total += size
            if size > biggest_size:
                biggest_size = size
                biggest = path
    return total, biggest, biggest_size

def copything(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)

def gzip_matching_files(directory: Path, globs: List[str]) -> None:
    for path in directory.rglob("*"):
        if path.is_file() and any(path.match(g) for g in globs):
            gz_path = path.with_suffix(path.suffix + ".gz")
            with path.open("rb") as f_in, gzip.open(gz_path, "wb", compresslevel=9) as f_out:
                shutil.copyfileobj(f_in, f_out)
            path.unlink()

def read_metadata(metadata_file: Path) -> Dict[str, Any]:
    if metadata_file.exists():
        with metadata_file.open() as f:
            try:
                data: Dict[str, Any] = json.load(f)
                return data
            except json.JSONDecodeError:
                pass
    return {}

def save_metadata(metadata_file: Path, data: Dict[str, Any]) -> None:
    with metadata_file.open("w") as f:
        json.dump(data, f)

def run_branch(bc: config.BranchConfig, log_name: str) -> int:
    log(f"Running branch {bc.branch_name} on repo {bc.repo_name}")
    info: Dict[str, str] = {}
    slack_output = slack.make_output(bc.slack_token, bc.repo_name)

    if bc.base_url:
        import urllib.parse
        info["logurl"] = bc.base_url + "logs/" + urllib.parse.quote(log_name)

    run(["git", "-C", bc.branch_dir, "reset", "--hard", f"origin/{bc.branch_name}"], check=True)
    run(["git", "-C", bc.branch_dir, "submodule", "update", "--init", "--recursive", "--force"], check=True)

    out = run(
        ["git", "-C", bc.branch_dir, "rev-parse", f"origin/{bc.branch_name}"],
        capture_output=True, check=True
    ).stdout.decode("ascii").strip()

    start = datetime.now()
    try:
        to = parse_time(bc.timeout)
        cmd = ["make", "-C", str(bc.branch_dir), "nightly"]
        
        if bc.report_dir:
            if bc.report_dir.exists():
                shutil.rmtree(bc.report_dir, ignore_errors=True)
            bc.report_dir.mkdir(parents=True, exist_ok=True)

        result = run(cmd, timeout=to)
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, cmd)

    except subprocess.TimeoutExpired as e:
        log(f"Run on branch {bc.branch_name} timed out after {format_time(e.timeout)}")
        failure = "timeout"
    except subprocess.CalledProcessError:
        failure = "failure"
    else:
        log(f"Successfully ran on branch {bc.branch_name}")
        failure = ""

    metadata = read_metadata(bc.metadata_file)
    metadata["commit"] = out
    metadata["time"] = time.time()
    save_metadata(bc.metadata_file, metadata)

    if bc.report_dir and bc.report_dir.exists():
        try:
            if bc.gzip:
                log(f"GZipping all {bc.gzip} files")
                gzip_matching_files(bc.report_dir, shlex.split(bc.gzip))

            total, biggest, _ = tree_size(bc.report_dir)
            if bc.warn_report and total > bc.warn_report:
                assert biggest is not None
                rel = biggest.relative_to(bc.report_dir)
                msg = (
                    f"Report size {format_size(total)} exceeds limit {format_size(bc.warn_report)}; "
                    f"largest file `{rel}`"
                )
                log(f"Report `{bc.branch_name}` is {format_size(total)}; largest file `{rel}`")
                if slack_output:
                    slack_output.warn("report-size", msg)

            if "url" not in info and bc.base_url:
                name = f"{int(time.time())}:{bc.branch_filename}:{out[:8]}"
                dest_dir = bc.reports_dir / bc.repo_name / name

                if bc.report_dir.exists():
                    log(f"Publishing report directory {bc.report_dir} to {dest_dir}")
                    copything(bc.report_dir, dest_dir)
                    url_base = bc.base_url + "reports/" + bc.repo_name + "/" + name
                    info["url"] = url_base
                    if bc.image_file and bc.image_file.exists():
                        log(f"Linking image file {bc.image_file}")
                        path = bc.image_file.relative_to(bc.report_dir)
                        info["img"] = url_base + "/" + str(path)
                    shutil.rmtree(bc.report_dir, ignore_errors=True)
                else:
                    log(f"Report directory {bc.report_dir} does not exist")
        except OSError as e:
            msg = f"Error saving report: {e}"
            log(f"Error saving report for `{bc.branch_name}`: {e}")
            if slack_output:
                slack_output.warn("broken-report", msg)

    total, biggest, _ = tree_size(bc.branch_dir)
    if bc.warn_branch and total > bc.warn_branch:
        rel = biggest.relative_to(bc.branch_dir) if biggest else None
        detail = f"; largest file `{rel}`" if rel else ""
        msg = (
            f"Branch directory for {bc.branch_name} in {bc.repo_name} is "
            f"{format_size(total)} (limit {format_size(bc.warn_branch)}){detail}"
        )
        log(msg)
        if slack_output:
            slack_output.warn("branch-size", msg)

    info["result"] = f"*{failure}*" if failure else "success"
    info["time"] = format_time((datetime.now() - start).seconds)

    log_file = bc.logs_dir / log_name
    if log_file.exists():
        size = log_file.stat().st_size
        if bc.warn_log and size > bc.warn_log:
            msg = (
                f"Log file for branch {bc.branch_name} in {bc.repo_name} seems too big at "
                f"{format_size(size)} (limit {format_size(bc.warn_log)})"
            )
            log(msg)
            if slack_output:
                slack_output.warn("log-size", msg)

    if slack_output:
        log("Posting results of run to slack!")
        try:
            slack_output.post(bc.branch_name, info)
        except slack.SlackError as e:
            log(f"Slack error: {e}")

    return 1 if failure else 0


def main() -> int:
    if len(sys.argv) != 5:
        print(f"Usage: {sys.argv[0]} <config_file> <repo> <branch> <log_name>", file=sys.stderr, flush=True)
        return 2

    config_file, repo, branch, log_name = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    cfg = config.Config(config_file)
    bc = config.BranchConfig(cfg, repo, branch)
    return run_branch(bc, log_name)


if __name__ == "__main__":
    sys.exit(main())
