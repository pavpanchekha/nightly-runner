#!/usr/bin/env python3

import subprocess
import os
import sys
import time
from pathlib import Path

os.chdir("/data/pavpan/nightlies")
os.putenv("PATH", os.getenv("PATH") + ":/home/p92/bin/")

def get(user, project, branch):
    pproject = Path(project)
    pproject.mkdir(parents=True, exist_ok=True)
    if not (pproject / branch).is_dir():
        subprocess.run(["git", "clone", "https://github.com/" + user + "/" + project + ".git", project + "/" + branch])
    subprocess.run(["git", "-C", project + "/" + branch, "fetch", "origin", "--prune"])
    subprocess.run(["git", "-C", project + "/" + branch, "fetch", "origin", branch])
    subprocess.run(["git", "-C", project + "/" + branch, "checkout", branch])
    subprocess.run(["git", "-C", project + "/" + branch, "reset", "--hard", "origin/" + branch])

def all_branches(project):
    pproject = Path(project)
    if not (pproject / "master").is_dir():
        sys.stderr.write("Cannot find directory " + project + "/master\n")
        sys.stderr.flush()
    
    branches = subprocess.run(["git", "-C", project + "/master", "branch", "-r"], stdout=subprocess.PIPE).stdout.decode("utf8").strip().split("\n")
    return [branch.split("/")[1] for branch in branches]


def check_branch(project, branch):
    dir = Path(project) / branch
    last_commit = Path(project) / (branch + ".last-commit")
    if last_commit.is_file():
        last = last_commit.open().read()
        current = subprocess.run(["git", "-C", project + "/" + branch, "rev-parse", "origin/" + branch], stdout=subprocess.PIPE).stdout
        if last == current:
            sys.stderr.write("Branch " + branch + " has not changed since last run; skipping\n")
            sys.stderr.flush()
            return False
    if subprocess.run(["make", "-C", project + "/" + branch, "-n", "nightly"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        sys.stderr.write("Branch " + branch + " does not have nightly rule; skipping\n")
        sys.stderr.flush()
        return False
    return True

def filter_branches(project, branches):
    return [branch for branch in branches if check_branch(project, branch)]

def run(project, branch):
    if subprocess.run(["nice", "make", "-C", project + "/" + branch, "nightly" ]).returncode:
        sys.stderr.write("Running " + project + " on branch " + branch + " failed\n")
        sys.stderr.flush()
    current = subprocess.run(["git", "-C", project + "/" + branch, "rev-parse", "origin/" + branch], stdout=subprocess.PIPE).stdout
    Path(project) / (branch + ".last-commit").open("wb").write(current)

START = time.time()
    
def log(s):
    with open("last.log", "at") as f:
        f.write(str(int(time.time() - START)) + "\t" + s + "\n")

with open("last.log", "at") as f:
    f.write("\n\n")

log("Nightly script starting up at " + time.ctime(time.time()))

for github in sys.argv[1:]:
    log("Beginning nightly run for " + github)

    user, project = github.split("/")
    Path(project).mkdir(parents=True, exist_ok=True)

    # Redirect output to log file
    log("Redirecting output to " + project + "/out.log")
    outlog = open(project + "/out.log", "wt")
    _sout = sys.stdout
    _serr = sys.stderr
    sys.stdout = outlog
    sys.stderr = outlog
    
    log("Downloading all " + github + " branches")
    get(user, project, "master")
    branches = ["master"] + all_branches(project)
    for branch in branches:
        get(user, project, branch)

    log("Filtering " + github + " branches " + " ".join(branches))
    branches = filter_branches(project, branches)

    log("Running " + github + " branches " + " ".join(branches))
    for branch in branches:
        log("Running tests on " + github + " branch " + branch)
        run(project, branch)

    log("Finished nightly run for " + github)

    sys.stdout = _sout
    sys.stderr = _serr
    outlog.close()
