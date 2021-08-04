#!/usr/bin/env python3

from typing import List, Dict, Optional, Any
import os, sys, subprocess
from datetime import datetime, timedelta
from pathlib import Path
import urllib.request, urllib.error
import configparser
import json
import tempfile
import shlex

class Log:
    dir = Path("logs/").resolve()
    if not dir.is_dir(): dir.mkdir()

    def __init__(self):
        self.start = datetime.now()
        name = f"{self.start:%Y-%m-%d}-{self.start:%H%M%S}.log"
        self.path = self.dir / name

    def log(self, level : int, s : str):
        with self.path.open("at") as f:
            f.write("{}\t{}{}\n".format(datetime.now() - self.start, "    " * level, s))

    def run(self, level : int, cmd : List[str]):
        self.log(level, "Executing " + " ".join([shlex.quote(arg) for arg in cmd]))
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
            
    def create_sublog(self, name : str, branch : str):
        date = datetime.now()
        name = f"{date:%Y-%m-%d}-{date:%H%M%S}-{name}-{branch}.log"
        return (self.dir / name).open("wt")

    def __repr__(self):
        return str(self.path)


def get(name : str, url : str, branch : str, logger : Log):
    dir = Path(name)
    dir.mkdir(parents=True, exist_ok=True)
    if not (dir / branch).is_dir():
        logger.run(1, ["git", "clone", "--recursive", url, f"{name}/{branch}"])
    logger.run(1, ["git", "-C", f"{name}/{branch}", "fetch", "origin", "--prune"])
    logger.run(1, ["git", "-C", f"{name}/{branch}", "fetch", "origin", branch])
    logger.run(1, ["git", "-C", f"{name}/{branch}", "checkout", branch])
    logger.run(1, ["git", "-C", f"{name}/{branch}", "reset", "--hard", "origin/" + branch])

def all_branches(name : str, branch : str, logger : Log):
    dir = Path(name)
    if not (dir / branch).is_dir():
        logger.log(1, f"Cannot find directory {name}/{branch}")
        return []
    
    out = logger.run(1, ["git", "-C", f"{name}/{branch}", "branch", "-r"])
    branches = out.stdout.decode("utf8").strip().split("\n")
    branches = [branch.split("/")[1] for branch in branches]
    return [branch for branch in branches if not branch.startswith("HEAD") and branch != branch]

def check_branch(name : str, branch : str, logger : Log):
    dir = Path(name) / branch
    last_commit = Path(name) / (branch + ".last-commit")
    if last_commit.is_file():
        last = last_commit.open("rb").read()
        current = logger.run(1, ["git", "-C", name + "/" + branch, "rev-parse", "origin/" + branch]).stdout
        if last == current:
            logger.log(1, "Branch " + branch + " has not changed since last run; skipping")
            return False
    try:
        logger.run(1, ["make", "-C", name + "/" + branch, "-n", "nightly"])
        return True
    except subprocess.CalledProcessError:
        logger.log(1, "Branch " + branch + " does not have nightly rule; skipping")
        return False

def run(name : str, branch : str, logger : Log, fd=sys.stderr, timeout : Optional[float]=None):
    success = ""
    logger.log(2, f"Running branch {branch}")
    try:
        result = subprocess.run(["nice", "make", "-C", name + "/" + branch, "nightly"], check=True, stdout=fd, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        assert timeout, "If timeout happened it must have been set"
        logger.log(2, f"Run on branch {branch} timed out after {timedelta(seconds=timeout)}")
        success = "timeout"
    except subprocess.CalledProcessError:
        logger.log(2, f"Run on branch {branch} failed")
        success = "failure"
    else:
        logger.log(2, "Successfully ran " + name + " on branch " + branch)

    out = logger.run(2, ["git", "-C", f"{name}/{branch}", "rev-parse", f"origin/{branch}"])
    with (Path(name) / (branch + ".last-commit")).open("wb") as last_commit_fd:
        last_commit_fd.write(out.stdout)

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

def build_slack_blocks(name : str, runs : Dict[str, Dict[str, Any]], baseurl : str):
    if not baseurl.endswith("/"): baseurl = baseurl + "/"

    blocks = []
    for branch, info in runs.items():
        result = info["result"]
        time = info["time"]
        text = f"Branch `{branch}` was a {result} in {time}"
        if "emoji" in info:
            text += " " + info["emoji"]

        block : Dict[str, Any] = {
            "type": "section",
            "text": { "type": "mrkdwn", "text": text },
        }
        if "success" != result:
            file = os.path.basename(info["file"])
            block["accessory"] = {
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": "Error Log",
                },
                "url": baseurl + file,
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
            if k in ["url", "emoji", "result", "time", "img", "file"]: continue
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

def post_to_slack(data : Any, url : str, logger : Log):
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
    LOG.log(0, f"Nightly script starting up on {datetime.now():%Y-%m-%d at %H:%M:%S}")
    
    config = configparser.ConfigParser()
    config.read("nightlies.conf")
    LOG.log(0, "Loaded configuration for " + ", ".join(set(config.keys()) - set(["DEFAULT"])))

    for name, configuration in config.items():
        if name == "DEFAULT": continue

        LOG.log(1, "Beginning nightly run for " + name)
        if "url" in configuration:
            url = configuration["url"]
        elif "github" in configuration:
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
            branches = all_branches(name, default, logger=LOG)
            for branch in branches:
                get(name, url, branch, logger=LOG)
            branches.append(default)
        
            LOG.log(1, "Filtering branches " + ", ".join(branches))
            branches = [branch for branch in branches if check_branch(name, branch, logger=LOG)]
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
                with LOG.create_sublog(name, branch) as fd:
                    t = datetime.now()
                    to = parse_time(configuration.get("timeout"))
                    success = run(name, branch, logger=LOG, fd=fd, timeout=to)
                    info = NR.info()
                    info["result"] = f"*{success}*" if success else "success"
                    info["time"] = str(datetime.now() - t)
                    info["file"] = fd.name
                    runs[branch] = info
                NR.reset()
        
            if "slack" in configuration and "baseurl" in config["DEFAULT"]:
                url = configuration["slack"]
                baseurl : str = config["DEFAULT"]["baseurl"]
                data = build_slack_blocks(name, runs, baseurl)
                if data:
                    LOG.log(2, f"Posting results of {name} run to slack!")
                    post_to_slack(data, url, logger=LOG)
        except subprocess.CalledProcessError as e :
            LOG.log(1, "Process " + str(e.cmd) + " returned error code " + str(e.returncode))
        finally:
            LOG.log(1, "Finished nightly run for " + name)

    LOG.log(0, "Finished nightly run for today")
