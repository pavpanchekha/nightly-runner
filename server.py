#!/usr/bin/env python3

from typing import Optional
from dataclasses import dataclass
import bottle
from pathlib import Path
import nightlies
import tempfile
import subprocess
import sys
import os
import json
import signal
import time
import shutil
import status

CONF_FILE = "conf/nightlies.conf"

@dataclass
class NightlyJob:
    job_id: str
    repo: str
    branch: str
    log: str
    last_print: Optional[float] = None
    elapsed: Optional[float] = None

def get_nightly_jobs(log_dir: Path) -> list[NightlyJob]:
    """Query slurm for running nightly jobs."""
    result = subprocess.run(
        ["squeue", "--Format=Name:500,JobID:500,Comment:500,TimeUsed:500,State:500", "--noheader"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"get_nightly_jobs: squeue failed: {result.stderr}", file=sys.stderr)
        return []
    jobs = []
    for line in result.stdout.splitlines():
        if not line.startswith("nightly:"):
            continue
        job_name, job_id, log, time_used, state = line.strip().split()
        if state != "RUNNING":
            continue
        repo, branch = job_name.removeprefix("nightly:").split(":", 1)
        last_print = None
        try:
            last_print = time.time() - os.path.getmtime(log_dir / log)
        except FileNotFoundError:
            pass
        elapsed = parse_slurm_time(time_used)
        jobs.append(NightlyJob(job_id, repo, branch, log, last_print, elapsed))
    return jobs

def parse_slurm_time(s: str) -> float:
    parts = s.split("-")
    days = int(parts[0]) if len(parts) == 2 else 0
    hms = parts[-1].split(":")
    h, m, sec = (int(hms[0]), int(hms[1]), int(hms[2])) if len(hms) == 3 else (0, int(hms[0]), int(hms[1]))
    return days * 86400 + h * 3600 + m * 60 + sec

def edit_conf_url(runner : nightlies.NightlyRunner) -> Optional[str]:
    if "confedit" in runner.config.defaults():
        return runner.config.defaults()["confedit"]

    conf_repo = runner.config.defaults().get("conf")
    conf_branch = runner.config.defaults().get("confbranch", "main")
    if conf_repo and conf_repo.startswith("http"):
        return conf_repo
    elif not conf_repo or ":" in conf_repo:
        return None
    else:
        return f"https://github.com/{conf_repo}/edit/{conf_branch}/{runner.config_file.name}"

def load():
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    runner.load_pid()

    for repo in runner.repos:
        repo.read()

    jobs = get_nightly_jobs(runner.log_dir)

    running = False
    if runner.data and "pid" in runner.data:
        try:
            # Does not actually kill, but does check if pid exists
            os.kill(runner.data["pid"], 0)
        except OSError:
            pass
        else:
            running = True

    current = dict(runner.data) if runner.data else None

    if current and "repo" in current:
        current["nr_action"] = "running" if jobs else "syncing"
        current["nr_repo"] = current["repo"]

    logins = set([
        line.split()[0].decode("utf8", errors="replace")
        for line in subprocess.run(
                ["who"], check=True, stdout=subprocess.PIPE
        ).stdout.split(b'\n')
        if line
    ])

    system_state = status.system_state_html()

    return {
        "runner": runner,
        "current": current,
        "running": running,
        "branches": jobs,
        "baseurl": runner.base_url,
        "confurl": edit_conf_url(runner),
        "system_state": system_state,
        "logins": logins,
    }

@bottle.route("/")
@bottle.view("index.view")
def index():
    return load()

@bottle.route("/docs")
@bottle.view("docs.view")
def docs():
    return load()

@bottle.route("/static/<filepath:path>")
def server_static(filepath):
    return bottle.static_file(filepath, root='static/')

@bottle.route("/robots.txt")
def robots_txt():
    return bottle.static_file("robots.txt", root='static/')

@bottle.route("/dryrun", ["GET", "POST"])
def dryrun():
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    runner.config["DEFAULT"]["dryrun"] = "true"
    run_nightlies(runner.config)
    bottle.redirect("/")
    
@bottle.post("/fullrun")
def fullrun():
    run_nightlies()
    bottle.redirect("/")
    
@bottle.post("/runnow")
def runnow():
    repo = bottle.request.forms.get('repo')
    branch = bottle.request.forms.get('branch')
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    runner.load_pid()
    if runner.data and "pid" in runner.data:
        try:
            os.kill(runner.data["pid"], 0)
        except OSError:
            pass
        else:
            raise bottle.HTTPError(409, "Nightly sync already running")
    for r in runner.repos:
        if r.name == repo:
            r.read()
            if branch in r.branches and "queued" in r.branches[branch].badges:
                branch_filename = r.branches[branch].filename
                raise bottle.HTTPError(409, f"Job nightly:{repo}:{branch_filename} already queued")
            break
    for section in runner.config.sections():
        if repo == section or section.endswith("/" + repo):
            runner.config[section]["branches"] = branch
            runner.config[section]["always"] = branch
        else:
            runner.config.remove_section(section)
    run_nightlies(runner.config)
    bottle.redirect("/")

@bottle.post("/rmbranch")
def rmbranch():
    repo_name = bottle.request.forms.get('repo')
    branch = bottle.request.forms.get('branch')
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    for repo in runner.repos:
        if repo.name == repo_name:
            try:
                shutil.rmtree(nightlies.Branch(repo, branch).dir)
                subprocess.run(["git", "-C", repo.checkout, "worktree", "prune"], check=True)
            except FileNotFoundError:
                pass
    bottle.redirect("/dryrun")

@bottle.post("/killsync")
def killsync():
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    runner.load_pid()
    pid = (runner.data or {}).get("pid")
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            print("/killsync: OSError:", str(e), file=sys.stderr)
    if runner.pid_file.exists():
        runner.pid_file.unlink()
    bottle.redirect("/")

@bottle.post("/killbranch")
def killbranch():
    job_id = bottle.request.forms.get('job_id')
    if job_id:
        subprocess.run(["scancel", job_id], check=False)
    else:
        print("/killbranch: no job_id provided", file=sys.stderr)
    bottle.redirect("/")
    
@bottle.post("/delete_pid")
def delete_pid():
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    if runner.pid_file.exists():
        runner.pid_file.unlink()
    bottle.redirect("/")

def run_nightlies(conf=None):
    if conf:
        conf.set("DEFAULT", "conffile", str(Path(CONF_FILE).resolve()))
        conf.set("DEFAULT", "clean", "false")
        with tempfile.NamedTemporaryFile(prefix="nightlies-", mode="wt", delete=False) as f:
            conf.write(f)
            fn = f.name
    else:
        fn = CONF_FILE
    subprocess.Popen(
        [sys.executable, nightlies.__file__, fn],
        cwd=os.path.dirname(nightlies.__file__),
        start_new_session=True,
    )

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        prog = 'server.py',
        description = 'Start the Nightly web UI server')
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--server", default="paste")
    parser.add_argument("--bind", default="0.0.0.0")
    args = parser.parse_args()
    bottle.run(server=args.server, host=args.bind, port=args.port)
