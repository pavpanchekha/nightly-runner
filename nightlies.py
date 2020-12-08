#!/usr/bin/env python3

import subprocess
import os
import sys
import time
from datetime import datetime
from pathlib import Path
import contextlib
import configparser
import urllib.request, urllib.error
import json
import tempfile

def get(user, project, branch, fd=sys.stdout):
    pproject = Path(project)
    pproject.mkdir(parents=True, exist_ok=True)
    if not (pproject / branch).is_dir():
        subprocess.run(["git", "clone", "https://github.com/" + user + "/" + project + ".git", project + "/" + branch], stdout=fd, stderr=subprocess.STDOUT)
    subprocess.run(["git", "-C", project + "/" + branch, "fetch", "origin", "--prune"], stdout=fd, stderr=subprocess.STDOUT)
    subprocess.run(["git", "-C", project + "/" + branch, "fetch", "origin", branch], stdout=fd, stderr=subprocess.STDOUT)
    subprocess.run(["git", "-C", project + "/" + branch, "checkout", branch], stdout=fd, stderr=subprocess.STDOUT)
    subprocess.run(["git", "-C", project + "/" + branch, "reset", "--hard", "origin/" + branch], stdout=fd, stderr=subprocess.STDOUT)

def all_branches(project, fd=sys.stdout):
    pproject = Path(project)
    if not (pproject / "master").is_dir():
        fd.write("Cannot find directory " + project + "/master\n")
        fd.flush()
    
    branches = subprocess.run(["git", "-C", project + "/master", "branch", "-r"], stdout=subprocess.PIPE, stderr=fd).stdout.decode("utf8").strip().split("\n")
    branches = [branch.split("/")[1] for branch in branches]
    return [branch for branch in branches if not branch.startswith("HEAD") and branch != "master"]


def check_branch(project, branch, fd=sys.stderr):
    dir = Path(project) / branch
    last_commit = Path(project) / (branch + ".last-commit")
    if last_commit.is_file():
        last = last_commit.open("rb").read()
        current = subprocess.run(["git", "-C", project + "/" + branch, "rev-parse", "origin/" + branch], stdout=subprocess.PIPE, stderr=fd).stdout
        if last == current:
            fd.write("Branch " + branch + " has not changed since last run; skipping\n")
            fd.flush()
            return False
    if subprocess.run(["make", "-C", project + "/" + branch, "-n", "nightly"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        fd.write("Branch " + branch + " does not have nightly rule; skipping\n")
        fd.flush()
        return False
    return True

def run(project, branch, fd=sys.stderr):
    success = True
    if subprocess.run(["nice", "make", "-C", project + "/" + branch, "nightly" ], stdout=fd, stderr=subprocess.STDOUT).returncode:
        fd.write("Running " + project + " on branch " + branch + " failed\n")
        fd.flush()
        success = False

    current = subprocess.run(["git", "-C", project + "/" + branch, "rev-parse", "origin/" + branch], stdout=subprocess.PIPE, stderr=fd).stdout
    with (Path(project) / (branch + ".last-commit")).open("wb") as fd:
        fd.write(current)

    return success

def build_slack_blocks(user, project, runs):
    blocks = []
    for branch, info in runs.items():
        result = info["result"]
        time = info["time"]
        text = "Branch `{branch}` was a {result} in {time}"
        if "emoji" in info:
            text += " " + info["emoji"]

        block = {
            "type": "section",
            "text": { "type": "mrkdwn", "text": text },
        }
        if "url" in info:
            block["accessory"] = {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "View Report",
                },
                "url": info["url"]
            }
        fields = []
        for k, v in info.items():
            if k in ["url", "emoji", "result", "time"]: continue
            fields.append({
                "type": "mrkdwn",
                "text": "*" + k + "*",
            })
            fields.append({
                "type": "mrkdwn",
            "text": v,
            })
        block["fields"] = fields
        blocks.append(block)
    return { "text": "Nightly data for {}/{}".format(user, project), "blocks": blocks }

def post_to_slack(data, url, fd=sys.stderr):
    req = urllib.request.Request(url, data=json.dumps(data).encode("utf8"), method="POST")
    req.add_header("Content-Type", "application/json; charset=utf8")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            fd.log(f"Slack returned response {response.status} {response.reason}")
    except urllib.error.HTTPError as exc:
        fd.log(f"Slack error: {exc.code} {exc.reason}, because {exc.read()}")

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

    def __repr__(self):
        return str(self.path)

class NightlyResults:
    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory(prefix="nightly")
        self.cwdir = os.getcwd()
        self.oldpath = os.getenv("PATH")
        self.infofile = Path(self.dir.name, "info")
        self.cmdfile = Path(self.dir.name, "nightly-results")

        os.chdir("/data/pavpan/nightlies")
        os.putenv("PATH", self.dir.name + ":/home/p92/bin/:" + self.oldpath)
        self.infofile.touch()
        with self.cmdfile.open("w") as f:
            f.write(f"#!/bin/bash\necho \"$@\" >> '{self.infofile}'\n")
        self.cmdfile.chmod(0o700)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        os.chdir(self.cwdir)
        os.putenv("PATH", self.oldpath)
        self.dir.cleanup()

    def info(self):
        out = {}
        with self.infofile.open() as f:
            for line in f:
                key, value = line.split(" ", 1)
                out[key] = value
        return out

    def reset(self):
        with self.infofile.open("w") as f:
            pass

with NightlyResults() as NR:
    LOG = Log()
    LOG.log("Nightly script starting up at " + time.ctime(time.time()))
    
    if len(sys.argv) > 1:
        config = { repo: {} for repo in sys.argv[1:] }
    else:
        config = configparser.ConfigParser()
        config.read("nightlies.conf")
    
    LOG.log("Running nightlies for " + ", ".join(config.keys()))
    for github, configuration in config.items():
        if github == "DEFAULT": continue
        LOG.log("Beginning nightly run for " + github)
    
        user, project = github.split("/")
        Path(project).mkdir(parents=True, exist_ok=True)
    
        # Redirect output to log file
        outlog = Log(project=project)
        runs = {}
        with outlog.open() as fd:
            LOG.log("Redirecting output to {}".format(outlog))
        
            LOG.log("Downloading all " + github + " branches")
            get(user, project, "master", fd=fd)
            branches = ["master"] + all_branches(project, fd=fd)
            for branch in branches:
                get(user, project, branch, fd=fd)
    
            LOG.log("Filtering " + github + " branches " + " ".join(branches))
            branches = [branch for branch in branches if check_branch(project, branch, fd=fd)]
    
            LOG.log("Running " + github + " branches " + " ".join(branches))
            for branch in branches:
                LOG.log("Running tests on " + github + " branch " + branch)
                branchlog = Log(project=project, branch=branch)
                with branchlog.open() as fd:
                    t = time.time()
                    success = run(project, branch, fd=fd)
                    dt = time.time() - t
                    info = NR.info()
                    info["result"] = "success" if success else "*failure*"
                    info["time"] = f"{dt:.1f}s"
                    runs[branch] = info
                NR.reset()
    
            if "slack" in configuration:
                url = configuration["slack"]
                data = build_slack_blocks(user, project, runs)
                if data:
                    LOG.log("Posting results of run to slack!")
                    post_to_slack(data, url, fd=LOG)
    
            LOG.log("Finished nightly run for " + github)
