#!/usr/bin/env python3

import bottle
from pathlib import Path
import nightlies
import configparser
import tempfile
import subprocess
import sys
import os

RUNNING_NIGHTLIES = []

@bottle.route("/")
@bottle.view("index.view")
def index():
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()

    if runner.pid_file.exists():
        try:
            with runner.pid_file.open("r") as f:
                current_process = json.load(f)
        except OSError:
            current_process = None
    else:
        current_process = None

    for repo in runner.repos:
        repo.branches = {}
        for fn in repo.dir.iterdir():
            if not fn.is_dir(): continue
            if fn in repo.ignored_files: continue
            repo.branches[fn.name] = nightlies.Branch(repo, fn.name)

    return { "runner": runner, "current": current_process }

@bottle.post("/dryrun")
def dryrun():
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    runner.config["DEFAULT"]["dryrun"] = "true"
    run_nightlies(runner.config)
    bottle.redirect("/")
    
@bottle.post("/runnow")
def runnow():
    repo = request.forms.get('repo')
    branch = request.forms.get('branch')
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    for repo_name in runner.config.sections():
        if repo_name == "DEFAULT":
            continue
        elif repo == repo_name or repo_name.endswith("/" + repo):
            runner.config[repo_name]["branches"] = branch
        else:
            runner.config.remove_section(repo_name)
    run_nightlies(runner.config)
    bottle.redirect("/")

@bottle.post("/runnext")
def runnext():
    repo_name = request.forms.get('repo')
    branch = request.forms.get('branch')
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    for repo in runner.repos:
        if repo.name == repo_name:
            Branch(repo, branch).lastcommit.unlink(missing_ok=True)
    bottle.redirect("/")

def run_nightlies(conf):
    with tempfile.NamedTemporaryFile(prefix="nightlies-", mode="wt", delete=False) as f:
        conf.write(f)
    RUNNING_NIGHTLIES.append(
        subprocess.Popen(
            [sys.executable, nightlies.__file__, f.name],
            cwd=os.path.dirname(nightlies.__file__)))

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = 9000

    bottle.run(host="0.0.0.0", port=port)
