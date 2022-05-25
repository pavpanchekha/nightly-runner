#!/usr/bin/env python3

import bottle
from pathlib import Path
import nightlies

@bottle.route("/")
@bottle.view("index.view")
def index():
    dir = Path("logs/")
    if not dir.is_dir(): return {}
    
    dates = {}
    repos = {}

    for fn in sorted(dir.iterdir()):
        when, *what = nightlies.Log.parse(fn.name)
        if when.date() not in dates:
            data = {
                "log": fn,
                "when": when,
                "runs": [],
            }
            dates[when.date()] = data
        if what:
            name, branch = what
            data = {
                "log": fn,
                "when": when,
                "name": name,
                "branch": branch,
            }
            dates[when.date()]["runs"].append(data)
            repos.setdefault(name, {})[branch] = data

    return { "dates": dates, "repos": repos}

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
