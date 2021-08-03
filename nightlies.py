#!/usr/bin/env python3

from typing import List, Dict, Optional
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
import shlex

BASEURL = "http://warfa.cs.washington.edu/nightlies/"

def get(name : str, url : str, branch : str, logger : Log):
    pproject = Path(project)
    pproject.mkdir(parents=True, exist_ok=True)
    if not (pproject / branch).is_dir():
        logger.run(1, ["git", "clone", "--recursive", url, f"{name}/{branch}"])
    logger.run(1, ["git", "-C", f"{name}/{branch}", "fetch", "origin", "--prune"])
    logger.run(1, ["git", "-C", f"{name}/{branch}", "fetch", "origin", branch])
    logger.run(1, ["git", "-C", f"{name}/{branch}", "checkout", branch])
    logger.run(1, ["git", "-C", f"{name}/{branch}", "reset", "--hard", "origin/" + branch])

def all_branches(project : str, branch : str, logger : Log):
    pproject = Path(project)
    if not (pproject / branch).is_dir():
        logger.log(1, f"Cannot find directory {project}/{branch}")
        return []
    
    out = logger.run(1, ["git", "-C", f"{project}/{master}", "branch", "-r"])
    branches = out.stdout.decode("utf8").strip().split("\n")
    branches = [branch.split("/")[1] for branch in branches]
    return [branch for branch in branches if not branch.startswith("HEAD") and branch != branch]

def check_branch(project : str, branch : str, logger : Log):
    dir = Path(project) / branch
    last_commit = Path(project) / (branch + ".last-commit")
    if last_commit.is_file():
        last = last_commit.open("rb").read()
        current = logger.run(1, ["git", "-C", project + "/" + branch, "rev-parse", "origin/" + branch]).stdout
        if last == current:
            logger.log(1, "Branch " + branch + " has not changed since last run; skipping")
            return False
    try:
        logger.run(1, ["make", "-C", project + "/" + branch, "-n", "nightly"])
        return True
    except subprocess.CalledProcessError:
        logger.log(1, "Branch " + branch + " does not have nightly rule; skipping")
        return False

def run(project : str, branch : str, logger : Log, fd=sys.stderr, timeout : Optional[float]=None):
    success = ""
    logger.write(2, f"Running branch {branch}\n")
    try:
        result = subprocess.run(["nice", "make", "-C", project + "/" + branch, "nightly"], check=True, stdout=fd, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        assert timeout, "If timeout happened it must have been set"
        logger.write(2, f"Run on branch {branch} timed out after {format_time(timeout)}\n")
        success = "timeout"
    except subprocess.CalledProcessError:
        logger.write(2, f"Run on branch {branch} failed\n")
        success = "failure"
    else:
        logger.write(2, "Successfully ran " + project + " on branch " + branch + "\n")

    out = logger.run(2, ["git", "-C", f"{project}/{branch}", "rev-parse", f"origin/{branch}"]).stdout
    with (Path(project) / (branch + ".last-commit")).open("wb") as last_commit_fd:
        last_commit_fd.write(current)

    return success

def format_time(ts : float):
    t = float(ts)
    if t < 120:
        return f"{t:.1f}s"
    elif t < 120*60:
        return f"{t/60:.1f}m"
    else:
        return f"{t/60/60:.1f}h"

def parse_time(to : str):
    if not to: return to
    units = {
        "hr": 3600, "h": 3600,
        "min": 60, "m": 60,
        "sec": 1, "s": 1,
    }
    for unit, multiplier in units.items():
        if to.endswith(unit):
            return float(to[:-len(unit)]) * multiplier
    return float(to)


def build_slack_blocks(name, runs):
    blocks = []
    for branch, info in runs.items():
        result = info["result"]
        time = format_time(info["time"])
        text = f"Branch `{branch}` was a {result} in {time}"
        if "emoji" in info:
            text += " " + info["emoji"]

        block = {
            "type": "section",
            "text": { "type": "mrkdwn", "text": text },
        }
        if "success" != result:
            url = f"{BASEURL}{datetime.now():%Y-%m-%d}-{project}-{branch}.log"
            block["accessory"] = {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "Error Log",
                },
                "url": url,
                "style": "primary",
            }
        elif "url" in info:
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
            if k in ["url", "emoji", "result", "time", "img"]: continue
            fields.append({
                "type": "mrkdwn",
                "text": "*" + k.title() + "*",
            })
            fields.append({
                "type": "mrkdwn",
            "text": v,
            })
        if fields:
            block["fields"] = fields
        blocks.append(block)
        if "img" in info:
            url, *alttext = info["img"].split(" ")
            blocks.append({
                "type": "image",
                "image_url": url,
                "alt_text": " ".join(alttext) or f"Image for {name} branch {branch}",
            })
    if blocks:
        return { "text": f"Nightly data for {name}", "blocks": blocks }
    else:
        return None

def post_to_slack(data, url : str, logger : Log):
    payload = json.dumps(data)
    req = urllib.request.Request(url, data=payload.encode("utf8"), method="POST")
    req.add_header("Content-Type", "application/json; charset=utf8")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            logger.log(2, f"Slack returned response {response.status} {response.reason}, because {response.read()}")
    except urllib.error.HTTPError as exc:
        reason = exc.read().decode('utf-8')
        logger.log(2, f"Slack error: {exc.code} {exc.reason}, because {reason}")
        logger.log(2, payload)

START = time.time()

class Log:
    dir = Path("logs/").resolve()
    if not dir.is_dir(): dir.mkdir()

    def __init__(self):
        date = datetime.now()
        name = f"{date:%Y-%m-%d}-{date:%H%M%S}.log"
        self.path = self.dir / name

    def log(self, level : int, s : str):
        with self.path.open("at") as f:
            f.write("{:.0f}\t{}{}\n".format(time.time() - START, "    " * level, s))

    def run(self, level : int, cmd : list[str]):
        self.log(level, f"Executing {shlex.join(cmd)}...")
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
            
    def create_sublog(project, branch):
        date = datetime.now()
        name = f"{date:%Y-%m-%d}-{date:%H%M%S}-{project}-{branch}.log"
        return (self.dir / name).open("wt")

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
            f.write(rf"""#!/bin/bash
if [[ "$1" == "url" && ! "$2" == *://* ]]; then
    printf "Invalid URL: '%s'\n" "$2"
    exit 1
else
    while [[ "$#" != "0" ]]; do
        printf '"%s" ' "$1" >> "{self.infofile}"
        shift
    done
    printf "\n" >> "{self.infofile}"
fi
""")
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
                if not line.strip(): continue
                key, *values = shlex.split(line)
                out[key] = " ".join(values)
        return out

    def reset(self):
        with self.infofile.open("w") as f:
            pass

with NightlyResults() as NR:
    LOG = Log()
    LOG.log(0, "Nightly script starting up at " + time.ctime(time.time()))
    
    config = configparser.ConfigParser()
    if len(sys.argv) > 1:
        for repo in sys.argv[1:]:
            config[repo] = {}
    else:
        config.read("nightlies.conf")
    
    LOG.log(0, "Running nightlies for " + ", ".join(config.keys()))
    for name, configuration in config.items():
        if name == "DEFAULT": continue

        LOG.log(1, "Beginning nightly run for " + name)
        if "url" in configuration:
            url = configuration["url"]
        elif "github" in configuration
            url = "git@github.com:" + configuration["github"] + ".git"
        else:
            user, name = name.split("/")
            url = f"git@github.com:{user}/{name}.git"

        Path(name).mkdir(parents=True, exist_ok=True)
    
        runs = {}
        try:
            LOG.log(1, "Downloading all " + name + " branches")
            default = configuration.get("master", "master")
            get(name, url, default, logger=LOG)
            branches = all_branches(project, default, logger=LOG)
            for branch in branches:
                get(name, url, branch, logger=LOG)
            branches.append(default)
        
            LOG.log(1, "Filtering branches " + ", ".join(branches))
            branches = [branch for branch in branches if check_branch(project, branch, logger=LOG)]
            if "baseline" in configuration:
                baseline = configuration["baseline"]
                if set(branches) - set([baseline]):
                    branches.append(baseline)
            elif configuration.get("run", "commit") == "always":
                if default not in branches:
                    branches.append(default)
        
            LOG.log(1, "Running branches " + " ".join(branches))
            for branch in branches:
                LOG.log(2, "Running tests on " + name + " branch " + branch)
                branchlog = Log(project=project, branch=branch)
                with LOG.create_sublog() as fd:
                    t = time.time()
                    to = parse_time(configuration.get("timeout"))
                    success = run(name, branch, fd=fd, timeout=to)
                    dt = time.time() - t
                    info = NR.info()
                    info["result"] = f"*{success}*" if success else "success"
                    info["time"] = str(dt)
                    runs[branch] = info
                NR.reset()
        
            if "slack" in configuration:
                url = configuration["slack"]
                data = build_slack_blocks(name, runs)
                if data:
                    LOG.log(2, f"Posting results of {name} run to slack!")
                    post_to_slack(data, url, logger=LOG)
        except subprocess.CalledProcessError as e :
            LOG.log(1, "Process " + str(e.cmd) + " returned error code " + str(e.returncode))
        finally:
            LOG.log(1, "Finished nightly run for " + name)
