#!/usr/bin/env python3

from typing import Optional
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

    running = False
    if runner.data and "pid" in runner.data:
        try:
            # Does not actually kill, but does check if pid exists
            os.kill(runner.data["pid"], 0)
        except OSError:
            pass
        else:
            running = True

    last_print = None
    if runner.data and "branch_log" in runner.data:
        log_file = runner.log_dir / runner.data["branch_log"]
        try:
            last_print = time.time() - os.path.getmtime(str(log_file))
        except FileNotFoundError:
            running = False
    system_state = status.system_state_html()

    return {
        "runner": runner,
        "current": runner.data,
        "running": running,
        "baseurl": runner.base_url,
        "confurl": edit_conf_url(runner),
        "system_state": system_state,
        "last_print": last_print,
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

@bottle.route("/dryrun")
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
            except FileNotFoundError:
                pass
    bottle.redirect("/dryrun")

@bottle.post("/kill")
def kill():
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    runner.load_pid()
    if runner.data and "branch_pid" in runner.data:
        try:
            os.kill(runner.data["pid"], signal.SIGTERM)
            runner.pid_file.unlink()
        except OSError as e:
            print("/kill: OSError:", str(e))
    else:
        print("/kill: no PID file")
    bottle.redirect("/")

@bottle.post("/killbranch")
def killbranch():
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    runner.load_pid()
    if runner.data and "branch_pid" in runner.data:
        try:
            os.kill(runner.data["branch_pid"], signal.SIGTERM)
        except OSError as e:
            print("/killbranch: OSError:", str(e))
    else:
        print("/killbranch: no PID file")

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
