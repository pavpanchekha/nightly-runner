#!/usr/bin/env python3

from typing import List, Dict, Optional, Any
import os, sys, subprocess
from datetime import datetime, timedelta
from pathlib import Path
import urllib.request, urllib.error
import configparser
import json
import tempfile
import shlex, shutil

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
        cmd = [str(arg) for arg in cmd]
        self.log(level, "Executing " + " ".join([shlex.quote(arg) for arg in cmd]))
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
            
    @classmethod
    def parse(cls, fn):
        if fn.endswith(".gz"): fn = fn[:-len(".gz")]
        assert fn.endswith(".log")
        fn = fn[:-len(".log")]

        if fn.count("-") >= 3:
            y, m, d, hms = fn.split("-", 3)
            if hms[:6].isdigit():
                h, mm, s, rest = hms[:2], hms[2:4], hms[4:6], hms[7:]
            else:
                rest = hms
                h, mm, s = 0, 0, 0
        else:
            y, m, d = fn.split("-")
            h, mm, s = 0, 0, 0
            rest = ""
        when = datetime(int(y), int(m), int(d), int(h), int(mm), int(s))
        if "-" in rest:
            name, branch = rest.split("-", 1)
            return when, name, branch
        else:
            return when,

    def __repr__(self):
        return str(self.path)

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
    blocks = []
    for branch, info in runs.items():
        result = info["result"]
        time = info["time"]
        text = f"Branch `{branch}` of `{name}` was a {result} in {time}"
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
    
def build_slack_fatal(name : str, text : str, baseurl : str):
    return {
        "text": f"Fatal error running nightlies for {name}", "blocks":{
            "type": "section",
            "text": { "type": "mrkdwn", "text": text },
        }
    }

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

class NightlyRunner:
    def __init__(self, config_file, NR):
        self.config_file = config_file
        self.NR = NR

    def load(self):
        self.config = configparser.ConfigParser()
        self.config.read(self.config_file)
        self.repos = []

        for name, configuration in self.config.items():
            if name == "DEFAULT":
                self.base_url = configuration.get("baseurl")
                if not self.base_url.endswith("/"): self.base_url += "/"
                self.log_dir = Path(configuration.get("logs", "logs")).resolve()
                self.dryrun = "dryrun" in configuration
                self.pid_file = Path(configuration.get("pid", "running.pid")).resolve()
            else:
                self.repos.append(Repository(self, name, configuration))

    def run(self):
        start = datetime.now().isoformat()

        self.log = Log()
        self.log.log(0, f"Nightly script starting up at {start}")
        self.log.log(0, "Loaded configuration for " + ", ".join([repo.name for repo in self.repos]))

        try:
            self.pid_file.touch(exists_ok=False)
        except FileExistsError:
            try:
                with self.pid_file.open("r") as f:
                    current_process = json.load(f)
                    self.log.log(0, f"Nightly already running on pid {current_process['pid']}")
            except OSError:
                self.log.log(0, f"Nightly already running")
        else:
            with self.pid_file.open("w") as f:
                json.dump({ "pid": os.getpid(), "start": start }, f)

        if self.dryrun:
            self.log.log(0, "Running in dry-run mode. No nightlies will be executed.")

        for repo in self.repos:
            try:
                repo.load()
                repo.filter()
                repo.run()
            except subprocess.CalledProcessError as e :
                repo.fatalerror = f"Process {e.cmd} returned error code {e.returncode}"
                self.log.log(0, repo.fatalerror)
            finally:
                self.log.log(0, f"Finished nightly run for {repo.name}")
            repo.post()

        self.pid_file.unlink()
        self.log.log(0, "Finished nightly run for today")

class Repository:
    def __init__(self, runner, name, configuration):
        self.runner = runner
        self.config = configuration

        self.slack_url = configuration.get("slack")

        if "url" in self.config:
            self.url = self.config["url"]
        else:
            self.url = "git@github.com:" + configuration.get("github", name) + ".git"

        self.name = name.split("/")[-1]
        self.dir = Path(self.name)
        self.ignored_files = {
            self.dir / path
            for path in shlex.split(self.config.get("ignore", ""))
        }
        self.fatalerror = None

    def load(self):
        self.runner.log.log(0, "Beginning nightly run for " + self.name)
        self.dir.mkdir(parents=True, exist_ok=True)

        default_branch = Branch(self, self.config.get("master", "master"))
        self.runner.log.log(1, f"Fetching default branch {default_branch.name}")
        default_branch.load()

        if "branches" in self.config:
            all_branches = self.config["branches"].split()
        else:
            git_branch = self.runner.log.run(2, ["git", "-C", default_branch.dir, "branch", "-r"])
            all_branches = [
                branch.split("/", 1)[-1] for branch
                in git_branch.stdout.decode("utf8").strip().split("\n")
            ]

        self.branches = {default_branch.name: default_branch}
        for branch_name in all_branches:
            if branch_name.startswith("HEAD"): continue
            if branch_name == default_branch.name: continue
            branch = Branch(self, branch_name)
            self.runner.log.log(1, f"Fetching branch {branch.name}")
            branch.load()
            self.branches[branch_name] = branch

        expected_files = { branch.dir for branch in self.branches.values() } | \
            { branch.lastcommit for branch in self.branches.values() } | \
            self.ignored_files
        self.runner.log.log(1, "Cleaning unnecessary files")
        for fn in self.dir.iterdir():
            if fn not in expected_files:
                self.runner.log.log(2, f"Deleting unknown file {fn}")
                if not self.runner.dryrun:
                    if fn.is_dir():
                        shutil.rmtree(str(fn))
                    else:
                        fn.unlink()

    def filter(self):
        self.runner.log.log(1, "Filtering branches " + ", ".join(self.branches))
        self.runnable = [branch for name, branch in self.branches.items() if branch.check()]
        for branch_name in self.config.get("baseline", "").split():
            baseline = self.branches[branch_name]
            if self.runnable and not baseline not in self.runnable:
                self.runner.log.log(2, f"Adding baseline branch {baseline.name}")
                self.runnable.append(baseline)
        for branch_name in self.config.get("always", "").split():
            branch = self.branches[branch_name]
            if branch not in self.runnable:
                self.runner.log.log(2, f"Adding always run on branch {branch.name}")
                self.runnable.append(branch)

    def run(self):
        if self.runnable:
            self.runner.log.log(1, "Running branches " + " ".join([b.name for b in self.runnable]))
            for branch in self.runnable:
                branch.run()
        else:
            self.runner.log.log(1, "No branches to run")

    def post(self):
        if not self.slack_url or not self.runner.base_url:
            self.runner.log.log(2, f"Not posting to slack, slack or baseurl not configured")
            return

        if self.fatalerror:
            data = build_slack_fatal(self.name, self.fatalerror, self.runner.base_url)
        else:
            runs = { branch.name : branch.info for branch in self.runnable }
            data = build_slack_blocks(self.name, runs, self.runner.base_url)

        if not self.runner.dryrun and data:
            self.runner.log.log(2, f"Posting results of {self.name} run to slack!")
            post_to_slack(data, self.slack_url, logger=self.runner.log)

class Branch:
    def __init__(self, repo, name):
        self.repo = repo
        self.name = name
        self.filename = self.name.replace(":", "::").replace("/", ":")
        self.dir = self.repo.dir / self.filename
        self.lastcommit = self.repo.dir / (self.filename + ".last-commit")

    def load(self):
        if not self.dir.is_dir():
            self.repo.runner.log.run(2, ["git", "clone", "--recursive", self.repo.url, self.dir])
        self.repo.runner.log.run(2, ["git", "-C", self.dir, "fetch", "origin", "--prune"])
        self.repo.runner.log.run(2, ["git", "-C", self.dir, "fetch", "origin", self.name])
        self.repo.runner.log.run(2, ["git", "-C", self.dir, "checkout", self.name])
        self.repo.runner.log.run(2, ["git", "-C", self.dir, "reset", "--hard", "origin/" + self.name])

    def check(self):
        current_commit = self.repo.runner.log.run(2, ["git", "-C", self.dir, "rev-parse", "origin/" + self.name]).stdout
        if self.lastcommit.is_file():
            with self.lastcommit.open("rb") as f:
                last_commit = f.read()
            if last_commit == current_commit:
                self.repo.runner.log.log(2, "Branch " + self.name + " has not changed since last run; skipping")
                return False
        return True

    def run(self):
        self.repo.runner.log.log(1, f"Running tests on branch {self.name}")
        date = datetime.now()
        log_name = f"{date:%Y-%m-%d}-{date:%H%M%S}-{self.repo.name}-{self.filename}.log"

        t = datetime.now()
        try:
            to = parse_time(self.repo.config.get("timeout"))
            cmd = ["nice", "make", "-C", str(self.dir), "nightly"]
            self.repo.runner.log.log(2, "Executing " + " ".join([shlex.quote(arg) for arg in cmd]))
            if not self.repo.runner.dryrun:
                with (self.repo.runner.log_dir / log_name).open("wt") as fd:
                    subprocess.run(cmd, check=True, stdout=fd, stderr=subprocess.STDOUT, timeout=to)
        except subprocess.TimeoutExpired:
            self.repo.runner.log.log(1, f"Run on branch {self.name} timed out after {format_time(timeout)}")
            failure = "timeout"
        except subprocess.CalledProcessError as e:
            self.repo.runner.log.log(1, f"Run on branch {self.name} failed with error code {e.returncode}")
            failure = "failure"
        else:
            self.repo.runner.log.log(1, f"Successfully ran on branch {self.name}")
            failure = ""

        if not self.repo.runner.dryrun:
            out = self.repo.runner.log.run(1, ["git", "-C", self.dir, "rev-parse", f"origin/{self.name}"])
            with self.lastcommit.open("wb") as last_commit_fd:
                last_commit_fd.write(out.stdout)

        self.info = self.repo.runner.NR.info()
        self.info["result"] = f"*{failure}*" if failure else "success"
        self.info["time"] = format_time((datetime.now() - t).seconds)
        self.info["file"] = log_name
        self.repo.runner.NR.reset()

if __name__ == "__main__":
    import sys
    conf_file = sys.argv[1] if len(sys.argv) > 1 else "nightlies.conf"
    with NightlyResults() as NR:
        runner = NightlyRunner(conf_file, NR)
        runner.load()
        runner.run()
