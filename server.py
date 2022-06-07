#!/usr/bin/env python3

import bottle
from pathlib import Path
import nightlies
import configparser
import tempfile
import subprocess
import sys

RUNNING_NIGHTLIES = []

@bottle.route("/")
@bottle.view("index.view")
def index():
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()

    for repo in runner.repos:
        repo.branches = {}
        for fn in repo.dir.iterdir():
            if not fn.is_dir(): continue
            if fn in repo.ignored_files: continue
            repo.branches[fn.name] = nightlies.Branch(repo, fn.name)

    return { "runner": runner }

@bottle.post("/dryrun")
def dryrun():
    runner = nightlies.NightlyRunner("nightlies.conf", None)
    runner.load()
    runner.config["dryrun"] = "true"
    run_nightlies(runner.config)

@bottle.get("/logs/<filepath:re:.*\.log>")
def log(filepath):
    return bottle.static_file(filepath, root="logs", mimetype="text/plain")

def run_nightlies(conf):
    with tempfile.NamedTemporaryFile(prefix="nightlies-", delete=False) as f:
        conf.write(f)
    RUNNING_NIGHTLIES.append(
        subprocess.Popen(
            [sys.executable, nightlies.__file__, f.name],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(nightlies.__file__)))

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = 9000

    bottle.run(host="0.0.0.0", port=port)
