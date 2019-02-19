#!/usr/bin/env python3

import subprocess
import os
import sys
import time
from datetime import datetime
from pathlib import Path
import contextlib

os.chdir("/data/pavpan/nightlies")
os.putenv("PATH", "/home/p92/bin/:" + os.getenv("PATH"))

def get(user, project, branch):
    pproject = Path(project)
    pproject.mkdir(parents=True, exist_ok=True)
    if not (pproject / branch).is_dir():
        subprocess.run(["git", "clone", "https://github.com/" + user + "/" + project + ".git", project + "/" + branch], stdout=sys.stdout, stderr=sys.stderr)
    subprocess.run(["git", "-C", project + "/" + branch, "fetch", "origin", "--prune"], stdout=sys.stdout, stderr=sys.stderr)
    subprocess.run(["git", "-C", project + "/" + branch, "fetch", "origin", branch], stdout=sys.stdout, stderr=sys.stderr)
    subprocess.run(["git", "-C", project + "/" + branch, "checkout", branch], stdout=sys.stdout, stderr=sys.stderr)
    subprocess.run(["git", "-C", project + "/" + branch, "reset", "--hard", "origin/" + branch], stdout=sys.stdout, stderr=sys.stderr)

def all_branches(project):
    pproject = Path(project)
    if not (pproject / "master").is_dir():
        sys.stderr.write("Cannot find directory " + project + "/master\n")
        sys.stderr.flush()
    
    branches = subprocess.run(["git", "-C", project + "/master", "branch", "-r"], stdout=subprocess.PIPE, stderr=sys.stderr).stdout.decode("utf8").strip().split("\n")
    branches = [branch.split("/")[1] for branch in branches]
    return [branch for branch in branches if not branch.startswith("HEAD") and branch != "master"]


def check_branch(project, branch):
    dir = Path(project) / branch
    last_commit = Path(project) / (branch + ".last-commit")
    if last_commit.is_file():
        last = last_commit.open("rb").read()
        current = subprocess.run(["git", "-C", project + "/" + branch, "rev-parse", "origin/" + branch], stdout=subprocess.PIPE, stderr=sys.stderr).stdout
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
    if subprocess.run(["nice", "make", "-C", project + "/" + branch, "nightly" ], stdout=sys.stdout, stderr=sys.stderr).returncode:
        sys.stderr.write("Running " + project + " on branch " + branch + " failed\n")
        sys.stderr.flush()
    current = subprocess.run(["git", "-C", project + "/" + branch, "rev-parse", "origin/" + branch], stdout=subprocess.PIPE, stderr=sys.stderr).stdout
    (Path(project) / (branch + ".last-commit")).open("wb").write(current)

START = time.time()

class Log:
    dir = Path("logs/").resolve()
    if not dir.is_dir(): dir.mkdir()

    def __init__(self, project=None, branch=None):
        name = "{date:%Y-%m-%d}{project}{branch}.log".format(
            date=datetime.now(),
            project=("-" + project) if project is not None else "",
            branch=("-" + branch) if branch is not None else "")
        self.path = self.dir / name

    def log(self, s):
        with self.path.open("at") as f:
            f.write("{:.0f}\t{}\n".format(time.time() - START, s))

    @contextlib.contextmanager
    def open(self):
        fd = self.path.open("at")
        try:
            yield fd
        finally:
            fd.close()

@contextlib.contextmanager
def output_to(fd):
    _sout, _serr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = fd, fd
    try:
        yield
    finally:
        sys.stdout, sys.stderr = _sout, _serr

LOG = Log()
LOG.log("Nightly script starting up at " + time.ctime(time.time()))
LOG.log("Running nightlies for " + ", ".join(sys.argv[1:]))

for github in sys.argv[1:]:
    LOG.log("Beginning nightly run for " + github)

    user, project = github.split("/")
    Path(project).mkdir(parents=True, exist_ok=True)

    # Redirect output to log file
    outlog = Log(project=project)
    with outlog.open() as fd, output_to(fd):
        LOG.log("Redirecting output to {}".format(outlog))
    
        LOG.log("Downloading all " + github + " branches")
        get(user, project, "master")
        branches = ["master"] + all_branches(project)
        for branch in branches:
            get(user, project, branch)

        LOG.log("Filtering " + github + " branches " + " ".join(branches))
        branches = filter_branches(project, branches)

        LOG.log("Running " + github + " branches " + " ".join(branches))
        for branch in branches:
            LOG.log("Running tests on " + github + " branch " + branch)
            branchlog = Log(project=project, branch=branch)
            with branchlog.open() as fd, output_to(fd):
                run(project, branch)

        LOG.log("Finished nightly run for " + github)
