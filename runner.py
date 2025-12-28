#!/usr/bin/env python3

from typing import TYPE_CHECKING, Any, Dict, List, Sequence, Union
from datetime import datetime
from pathlib import Path
import gzip, os, shlex, shutil, subprocess, time
import slack

if TYPE_CHECKING:
    from nightlies import Branch

SYSTEMD_SLICE = "nightlies.slice"

SYSTEMD_RUN_CMD = [
    "sudo",
    "systemd-run",
    "--collect",
    "--wait",
    "--pty",
    f"--uid={os.getuid()}",
    f"--gid={os.getgid()}",
    f"--slice={SYSTEMD_SLICE}",
    "--property=Delegate=yes",
    "--service-type=simple",
    "--setenv=TERM=dumb",
]

def format_time(ts: float) -> str:
    t = float(ts)
    if t < 120:
        return f"{t:.1f}s"
    elif t < 120*60:
        return f"{t/60:.1f}m"
    else:
        return f"{t/60/60:.1f}h"

def format_cmd(s: Sequence[Union[str, Path]]) -> str:
    if hasattr(shlex, "join"):
        return shlex.join([str(part) for part in s])
    else:
        return " ".join([
            str(part) if " " not in str(part) else '"{}"'.format(part)
            for part in s
        ])

def parse_time(to: str | None) -> float | None:
    if to is None: return to
    units = {
        "hr": 3600, "h": 3600,
        "min": 60, "m": 60,
        "sec": 1, "s": 1,
    }
    for unit, multiplier in units.items():
        if to.endswith(unit):
            return float(to[:-len(unit)]) * multiplier
    return float(to)

def parse_size(size: str | None) -> int | None:
    if size is None: return size
    units = {
        "kb": 1024, "k": 1024,
        "mb": 1024**2, "m": 1024**2,
        "gb": 1024**3, "g": 1024**3,
    }
    size = size.lower()
    for unit, multiplier in units.items():
        if size.endswith(unit):
            return int(float(size[:-len(unit)]) * multiplier)
    return int(size)

def format_size(size: int) -> str:
    units = ["KB", "MB", "GB", "TB", "PB"]
    s = float(size) / 1024
    for unit in units:
        if s < 1024:
            break
        s /= 1024
    return f"{s:.2f}{unit}"

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

def run_branch(branch: "Branch", log_name: str) -> None:
    branch.repo.runner.log(0, f"Running branch {branch.name} on repo {branch.repo.name}")
    info: Dict[str, str] = {}

    if branch.repo.runner.base_url:
        import urllib.parse
        info["logurl"] = branch.repo.runner.base_url + "logs/" + urllib.parse.quote(log_name)

    t = datetime.now()
    try:
        to = parse_time(branch.repo.config.get("timeout"))
        cmd = SYSTEMD_RUN_CMD + ["make", "-C", str(branch.dir), "nightly"]
        branch.repo.runner.log(1, f"Executing {format_cmd(cmd)}")
        if branch.report_dir:
            if branch.report_dir.exists():
                shutil.rmtree(branch.report_dir, ignore_errors=True)
            branch.report_dir.mkdir(parents=True, exist_ok=True)

        with (branch.repo.runner.log_dir / log_name).open("wt") as fd:
            process = subprocess.Popen(cmd, stdout=fd, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            branch.repo.runner.data["branch_pid"] = process.pid
            branch.repo.runner.save()
            try:
                returncode = process.wait(timeout=to)
                if returncode: raise subprocess.CalledProcessError(returncode, cmd)
            finally:
                process.kill()
                branch.repo.runner.exec(2, ["sudo", "systemctl", "stop", "nightlies.slice"])

    except subprocess.TimeoutExpired as e:
        branch.repo.runner.log(1, f"Run on branch {branch.name} timed out after {format_time(e.timeout)}")
        failure = "timeout"
    except subprocess.CalledProcessError as e:
        failure = "failure"
    else:
        branch.repo.runner.log(1, f"Successfully ran on branch {branch.name}")
        failure = ""

    out = (
        branch.repo.runner.exec(
            2, ["git", "-C", branch.dir, "rev-parse", f"origin/{branch.name}"]
        ).stdout.decode("ascii").strip()
    )
    branch.config["commit"] = out
    branch.config["time"] = time.time()
    branch.save_metadata()

    if branch.report_dir and branch.report_dir.exists():
        try:
            if branch.repo.config.get("gzip", ""):
                branch.repo.runner.log(2, f"GZipping all {branch.repo.config.get('gzip', '')} files")
                gzip_matching_files(branch.report_dir, shlex.split(branch.repo.config.get("gzip", "")))

            warn_size = parse_size(branch.repo.config.get("warn_size", "1gb"))
            total = 0
            biggest = None
            biggest_size = 0
            for root, _, files in branch.report_dir.walk():
                for name in files:
                    path = root / name
                    size = path.stat().st_size
                    total += size
                    if size > biggest_size:
                        biggest_size = size
                        biggest = path
            if warn_size and total > warn_size:
                assert biggest is not None
                rel = biggest.relative_to(branch.report_dir)
                msg = f"Report size {format_size(total)} exceeds limit {format_size(warn_size)}; largest file `{rel}`"
                branch.repo.runner.log(1, f"Report `{branch.name}` is {format_size(total)}; largest file `{rel}`")
                if branch.slack:
                    branch.slack.warn("report-size", msg)

            if "url" not in info:
                assert branch.repo.runner.base_url, f"Cannot publish, no baseurl configured"
                name = f"{int(time.time())}:{branch.filename}:{out[:8]}"
                dest_dir = branch.repo.runner.report_dir / branch.repo.name / name

                if branch.report_dir.exists():
                    branch.repo.runner.log(2, f"Publishing report directory {branch.report_dir} to {dest_dir}")
                    copything(branch.report_dir, dest_dir)
                    url_base = branch.repo.runner.base_url + "reports/" + branch.repo.name + "/" + name
                    info["url"] = url_base
                    if branch.image_file and branch.image_file.exists():
                        branch.repo.runner.log(2, f"Linking image file {branch.image_file}")
                        path = branch.image_file.relative_to(branch.report_dir)
                        info["img"] = url_base + "/" + str(path)
                    shutil.rmtree(branch.report_dir, ignore_errors=True)
                else:
                    branch.repo.runner.log(2, f"Report directory {branch.report_dir} does not exist")
        except OSError as e:
            msg = f"Error saving report: {e}"
            branch.repo.runner.log(1, f"Error saving report for `{branch.name}`: {e}")
            if branch.slack:
                branch.slack.warn("broken-report", msg)

    info["result"] = f"*{failure}*" if failure else "success"
    info["time"] = format_time((datetime.now() - t).seconds)

    log_file = branch.repo.runner.log_dir / log_name
    if log_file.exists() and log_file.stat().st_size > 10 * 1024 * 1024:
        size = log_file.stat().st_size
        msg = (
            f"Log file for branch {branch.name} in {branch.repo.name} "
            f"seems too big at {size/1024/1024:.1f}MB"
        )
        branch.repo.runner.log(1, msg)
        if branch.slack:
            branch.slack.warn("log-size", msg)

    if branch.slack:
        branch.repo.runner.log(1, "Posting results of run to slack!")
        try:
            branch.slack.post(branch.name, info)
        except slack.SlackError as e:
            branch.repo.runner.log(2, f"Slack error: {e}")
