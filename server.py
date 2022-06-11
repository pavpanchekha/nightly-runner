#!/usr/bin/env python3

import bottle
from pathlib import Path
import nightlies
import configparser
import tempfile
import subprocess
import sys
import os
import json
import signal

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

    if "pid" in current_process:
        try:
            os.kill(current["pid"], 0) # Does not actually kill, but does check if pid exists
        except OSError:
            running = False
        else:
            running = True

    return { "runner": runner, "current": current_process, "running": running }

@bottle.post("/dryrun")
def dryrun():
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    runner.config["DEFAULT"]["dryrun"] = "true"
    run_nightlies(runner.config)
    bottle.redirect("/")
    
@bottle.post("/fullrun")
def fullrun():
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    run_nightlies(runner.config)
    bottle.redirect("/")
    
@bottle.post("/runnow")
def runnow():
    repo = bottle.request.forms.get('repo')
    branch = bottle.request.forms.get('branch')
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    for section in runner.config.sections():
        if repo == section or section.endswith("/" + repo):
            runner.config[section]["branches"] = branch
            runner.config[section]["always"] = branch
        else:
            runner.config.remove_section(section)
    run_nightlies(runner.config)
    bottle.redirect("/")

@bottle.post("/runnext")
def runnext():
    repo_name = bottle.request.forms.get('repo')
    branch = bottle.request.forms.get('branch')
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    for repo in runner.repos:
        if repo.name == repo_name:
            try:
                nightlies.Branch(repo, branch).lastcommit.unlink()
            except FileNotFound:
                pass
    bottle.redirect("/")

@bottle.post("/kill")
def kill():
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    if runner.pid_file.exists():
        try:
            with runner.pid_file.open("r") as f:
                current_process = json.load(f)
                os.kill(current_process["pid"], signal.SIGTERM)
            runner.pid_file.unlink()
        except OSError:
            current_process = None
    else:
        current_process = None
    bottle.redirect("/")

@bottle.post("/killbranch")
def killbranch():
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    if runner.pid_file.exists():
        try:
            with runner.pid_file.open("r") as f:
                current_process = json.load(f)
                os.kill(current_process["branch_pid"], signal.SIGTERM)
        except OSError:
            current_process = None
    else:
        current_process = None
    bottle.redirect("/")
    
@bottle.post("/delete_pid")
def delete_pid():
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    if runner.pid_file.exists():
        runner.pid_file.unlink()
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
