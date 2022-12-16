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
import time

CONF_FILE = "conf/nightlies.conf"

RUNNING_NIGHTLIES = []

@bottle.route("/")
@bottle.view("index.view")
def index():
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()

    if runner.pid_file.exists():
        try:
            with runner.pid_file.open("r") as f:
                current_process = json.load(f)
        except (OSError, json.decoder.JSONDecodeError):
            current_process = None
    else:
        current_process = None

    for repo in runner.repos:
        repo.read()

    running = False
    if current_process and "pid" in current_process:
        try:
            # Does not actually kill, but does check if pid exists
            os.kill(current_process["pid"], 0)
        except OSError:
            pass
        else:
            running = True

    last_print = None
    if current_process and "branch_log" in current_process:
        log_file = runner.log_dir / current_process["branch_log"]
        try:
            last_print = time.time() - os.path.getmtime(str(log_file))
        except FileNotFoundError:
            running = False

    return {
        "runner": runner,
        "current": current_process,
        "running": running,
        "baseurl": runner.base_url,
        "last_print": last_print,
    }

@bottle.route("/robots.txt")
@bottle.view("robots.txt")
def robots_txt(): pass

@bottle.post("/dryrun")
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
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    for repo in runner.repos:
        if repo.name == repo_name:
            try:
                nightlies.Branch(repo, branch).lastcommit.unlink()
            except FileNotFoundError:
                pass
    bottle.redirect("/")

@bottle.post("/kill")
def kill():
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    if runner.pid_file.exists():
        try:
            with runner.pid_file.open("r") as f:
                current_process = json.load(f)
                os.kill(current_process["pid"], signal.SIGTERM)
            runner.pid_file.unlink()
        except OSError as e:
            print("/kill: OSError:", str(e))
            current_process = None
    else:
        print("/kill: no PID file")
        current_process = None
    bottle.redirect("/")

@bottle.post("/killbranch")
def killbranch():
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    if runner.pid_file.exists():
        try:
            with runner.pid_file.open("r") as f:
                current_process = json.load(f)
                os.kill(current_process["branch_pid"], signal.SIGTERM)
        except OSError as e:
            print("/killbranch: OSError:", str(e))
            current_process = None
    else:
        print("/killbranch: no PID file")
        current_process = None
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
        with tempfile.NamedTemporaryFile(prefix="nightlies-", mode="wt", delete=False) as f:
            conf.write(f)
            fn = f.name
    else:
        fn = CONF_FILE
    RUNNING_NIGHTLIES.append(
        subprocess.Popen(
            [sys.executable, nightlies.__file__, fn],
            cwd=os.path.dirname(nightlies.__file__),
            start_new_session=True,
        ))

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = 9000

    bottle.run(server="paste", host="0.0.0.0", port=port)
