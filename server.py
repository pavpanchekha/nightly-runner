#!/usr/bin/env python3

import bottle
from pathlib import Path
import nightlies

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

@bottle.get("/logs/<filepath:re:.*\.log>")
def log(filepath):
    return bottle.static_file(filepath, root="logs", mimetype="text/plain")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    else:
        port = 9000

    bottle.run(host="0.0.0.0", port=port)
