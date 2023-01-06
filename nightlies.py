#!/usr/bin/env python3

from typing import Dict, Any, List, Union, Optional
import os, sys, subprocess
from datetime import datetime, timedelta
from pathlib import Path
import configparser
import json
import tempfile
import shlex, shutil
import slack, apt

def format_time(ts : float) -> str:
    t = float(ts)
    if t < 120:
        return f"{t:.1f}s"
    elif t < 120*60:
        return f"{t/60:.1f}m"
    else:
        return f"{t/60/60:.1f}h"
    
def format_cmd(s : list[Any]) -> str:
    return shlex.join([str(part) for part in s])

def parse_time(to : Optional[str]) -> Optional[float]:
    if to is None: return to
    units = {
        "hr": 3600, "h": 3600,
        "min": 60, "m": 60,
        "sec": 1, "s": 1,
    }
    for unit, multiplier in units.items():
        if to.endswith(unit):
            return float(to[:-len(unit)]) * multiplier
    return float(to)

SYSTEMD_RUN_CMD = [
    "sudo", # There might not be a user session manager, so run using root's
    "systemd-run",
    "--collect", # If it fails, throw it away
    "--same-dir", # Keep current working dir (probably unneeded)
    "--wait", # Wait for it to finish
    "--pty", # Pass through stdio
    f"--uid={os.getuid()}", # As the current user
    f"--gid={os.getgid()}", # As the current group
    "--slice=nightlies.slice", # Run with the nightly resource limits
    "--service-type=exec", # It just execs a program
]

REPO_BADGES = [
    "baseline", # Run this branch if you run anything else
    "always", # Run this branch every day
    "never", # Never run this branch
]

class NightlyRunner:
    def __init__(self, config_file : str) -> None:
        self.config_file = Path(config_file)

    def update_system_repo(self, dir : str, branch : str) -> bool:
        self.log(1, f"Updating system {dir} repository")
        try:
            conf_commit = self.exec(2, ["git", "-C", dir, "rev-parse", "HEAD"]).stdout
            self.log(2, f"Commit {conf_commit.decode().strip()}")
            self.exec(2, ["git", "-C", dir, "fetch", "origin", "--prune"])
            self.exec(2, ["git", "-C", dir, "reset", "--hard", "origin/" + branch])
            conf_commit2 = self.exec(2, ["git", "-C", dir, "rev-parse", "HEAD"]).stdout
            self.log(2, f"Commit {conf_commit2.decode().strip()}")
        except subprocess.CalledProcessError as e:
            self.log(0, f"Process {format_cmd(e.cmd)} returned error code {e.returncode}")
            sys.exit(1)
        if conf_commit == conf_commit2:
            self.log(1, f"System {dir} repository up to date")
            return False
        else:
            self.log(1, f"System {dir} repository updated; will need to restart")
            return True

    def load(self) -> None:
        assert self.config_file.is_file(), f"Configuration file {self.config_file} is not a file"
        self.config = configparser.ConfigParser()
        self.config.read(str(self.config_file))
        self.repos = []

        defaults = self.config.defaults()
        self.base_url = defaults.get("baseurl")
        if self.base_url and not self.base_url.endswith("/"): self.base_url += "/"
        self.dir = Path(defaults.get("repos", ".")).resolve()
        self.log_dir = Path(defaults.get("logs", "logs")).resolve()
        self.dryrun = "dryrun" in defaults
        self.pid_file = Path(defaults.get("pid", "running.pid")).resolve()
        self.info_file = Path(defaults.get("info", "running.info")).resolve()
        self.config_file = Path(defaults.get("conffile", str(self.config_file))).resolve()

        for name in self.config.sections():
            self.repos.append(Repository(self, name, self.config[name]))

    def update(self) -> None:
        if self.config.getboolean("DEFAULT", "pullself", fallback=False):
            branch = self.config.defaults().get("selfbranch", "main")
            if self.update_system_repo(".", branch): self.restart()
        if self.config.getboolean("DEFAULT", "pullconf", fallback=False):
            conf_dir = os.path.dirname(self.config_file)
            branch = self.config.defaults().get("confbranch", "main")
            if self.update_system_repo(conf_dir, branch): self.restart()

    def restart(self) -> None:
        self.log(0, "Restarting nightly run due to updated system repositories")
        os.execv(sys.executable, ["python3"] + sys.argv)

    def exec(self, level : int, cmd : List[Union[str, Path]]) -> subprocess.CompletedProcess:
        self.log(level, f"Executing {format_cmd(cmd)}")
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)

    def log(self, level : int, s : str) -> None:
        with self.log_path.open("at") as f:
            f.write("{}\t{}{}\n".format(datetime.now() - self.start, "    " * level, s))

    def save(self) -> None:
        with self.pid_file.open("w") as f:
            json.dump(self.data, f)

    def add_info(self, cmd, *args) -> None:
        with self.info_file.open("a") as f:
            f.write(shlex.join([cmd] + list(args)) + "\n")

    def load_info(self) -> dict[str, str]:
        out = {}
        try:
            with self.info_file.open("r") as f:
                for line in f:
                    if not line.strip(): continue
                    cmd, *args = shlex.split(line)
                    out[cmd] = " ".join(args)
            # Clear the info file file
            self.info_file.open("w").close()
        except OSError as e:
            self.log(2, f"Error loading info file: {e}")
        return out

    def run(self) -> None:
        self.start = datetime.now()
        name = f"{self.start:%Y-%m-%d}-{self.start:%H%M%S}.log"
        self.log_path = self.log_dir / name
        self.log(0, f"Nightly script starting up at {self.start:%H:%M}")
        self.log(0, f"Loaded configuration file {self.config_file}")
        self.update()

        self.data = {
            "pid": os.getpid(),
            "start": self.start.isoformat(),
            "config": str(Path(self.config_file).resolve()),
            "log": str(self.log_path),
        }

        try:
            self.pid_file.touch(exist_ok=False)
        except FileExistsError:
            try:
                with self.pid_file.open("r") as f:
                    current_process = json.load(f)
                    self.log(0, f"Nightly already running on pid {current_process['pid']}")
                    return
            except (OSError, json.decoder.JSONDecodeError):
                self.log(0, f"Nightly already running")
                return
        else:
            self.save()

        if self.dryrun:
            self.log(0, "Running in dry-run mode. No nightlies will be executed.")

        if Path(".git/info").exists():
            with open(".git/info/exclude", "wt") as f:
                for repo in self.repos:
                    f.write(repo.dir.name + "\n")

        for repo in self.repos:
            try:
                self.data["repo"] = repo.name
                self.save()
                repo.load()
                repo.filter()
                repo.run()
            except subprocess.CalledProcessError as e:
                repo.fatalerror = f"Process {format_cmd(e.cmd)} returned error code {e.returncode}"
                self.log(1, repo.fatalerror)
            finally:
                repo.post()
                self.log(0, f"Finished nightly run for {repo.name}")
                del self.data["repo"]
                self.save()

        self.pid_file.unlink()
        self.log(0, "Finished nightly run for today")

class Repository:
    def __init__(self, runner : NightlyRunner, name : str, configuration : configparser.SectionProxy):
        self.runner = runner
        self.config = configuration

        self.slack_url = configuration.get("slack")
        self.run_all = False

        if "url" in self.config:
            self.url = self.config["url"]
        else:
            self.url = "git@github.com:" + configuration.get("github", name) + ".git"

        self.name = name.split("/")[-1]
        self.dir = runner.dir / self.name
        self.ignored_files = {
            self.dir / path
            for path in shlex.split(self.config.get("ignore", ""))
        }
        self.fatalerror: Optional[str] = None

    def load(self) -> None:
        self.runner.log(0, "Beginning nightly run for " + self.name)
        self.dir.mkdir(parents=True, exist_ok=True)

        apt_pkgs = self.config.get("apt", "").split()
        if apt_pkgs:
            updates = apt.check_updates(self.runner, apt_pkgs)
            if updates:
                if not self.runner.dryrun:
                    apt.install(self.runner, apt_pkgs)
                self.run_all = True

        default_branch = Branch(self, self.config.get("main", "main"))
        self.runner.log(1, f"Fetching default branch {default_branch.name}")
        default_branch.load()

        git_branch = self.runner.exec(2, ["git", "-C", default_branch.dir, "branch", "-r"])
        all_branches = [
            branch.split("/", 1)[-1] for branch
            in git_branch.stdout.decode("utf8").strip().split("\n")
        ]

        expected_files = self.ignored_files | {self.dir / b for b in all_branches} | \
            { self.dir / f"{b}.last-commit" for b in all_branches }
        self.runner.log(1, "Cleaning unnecessary files")
        for fn in self.dir.iterdir():
            if fn not in expected_files:
                self.runner.log(2, f"Deleting unknown file {fn}")
                if not self.runner.dryrun:
                    if fn.is_dir():
                        shutil.rmtree(str(fn))
                    else:
                        fn.unlink()

        if "branches" in self.config:
            all_branches = self.config["branches"].split()

        self.branches = {default_branch.name: default_branch}
        for branch_name in all_branches:
            if branch_name.startswith("HEAD"): continue
            if branch_name == default_branch.name: continue
            branch = Branch(self, branch_name)
            self.runner.log(1, f"Fetching branch {branch.name}")
            branch.load()
            self.branches[branch_name] = branch

        self.assign_badges()

    def read(self) -> None:
        self.branches = {}
        if self.dir.is_dir():
            for fn in self.dir.iterdir():
                if not fn.is_dir(): continue
                if fn in self.ignored_files: continue
                name = Branch.parse_filename(fn.name)
                self.branches[name] = Branch(self, name)
            self.assign_badges()

    def assign_badges(self) -> None:
        for field in REPO_BADGES:
            for branch_name in self.config.get(field, "").split():
                if branch_name in self.branches:
                    branch = self.branches[branch_name]
                    branch.badges.append(field)

        main_branch = self.config.get("main", "main")
        if main_branch in self.branches:
            self.branches[main_branch].badges.append("main")

    def filter(self) -> None:
        if self.run_all:
            self.runnable = list(self.branches.values())
            return

        self.runner.log(1, "Filtering branches " + ", ".join(self.branches))
        self.runnable = [branch for name, branch in self.branches.items() if branch.check()]
        for branch in self.branches.values():
            if "always" in branch.badges and branch not in self.runnable:
                self.runner.log(2, f"Adding always run on branch {branch.name}")
                self.runnable.append(branch)
            if "baseline" in branch.badges and branch not in self.runnable and self.runnable:
                self.runner.log(2, f"Adding baseline branch {branch.name}")
                self.runnable.append(branch)
            if "never" in branch.badges and branch in self.runnable:
                self.runner.log(2, f"Removing never run on branch {branch.name}")
                self.runnable.remove(branch)

    def run(self) -> None:
        if self.runnable:
            self.runner.log(1, "Running branches " + " ".join([b.name for b in self.runnable]))
            for branch in self.runnable:
                branch.run()
        else:
            self.runner.log(1, "No branches to run")

    def post(self) -> None:
        if not self.slack_url or not self.runner.base_url:
            self.runner.log(2, f"Not posting to slack, slack or baseurl not configured")
            return

        if self.fatalerror:
            data = slack.build_fatal(self.name, self.fatalerror, self.runner.base_url)
        else:
            runs = { branch.name : branch.info for branch in self.runnable if branch.info }
            if not runs: return
            data = slack.build_runs(self.name, runs, self.runner.base_url)

        if self.run_all:
            apt.post(data)

        if not self.runner.dryrun:
            slack.send(self.runner, self.slack_url, data)

class Branch:
    def __init__(self, repo : Repository, name : str):
        self.repo = repo
        self.name = name
        self.filename = self.name.replace("%", "%25").replace("/", "%2f")
        self.dir = self.repo.dir / self.filename
        self.lastcommit = self.repo.dir / (self.filename + ".last-commit")
        self.badges : list[str] = []

    def last_run(self):
        try:
            return os.path.getmtime(str(self.lastcommit))
        except FileNotFoundError:
            return -1

    @classmethod
    def parse_filename(cls, filename):
        return filename.replace("%2f", "/").replace("%25", "%")

    def load(self) -> None:
        if not self.dir.is_dir():
            self.repo.runner.exec(2, ["git", "clone", "--recursive", self.repo.url, self.dir])
        self.repo.runner.exec(2, ["git", "-C", self.dir, "fetch", "origin", "--prune"])
        self.repo.runner.exec(2, ["git", "-C", self.dir, "fetch", "origin", self.name])
        self.repo.runner.exec(2, ["git", "-C", self.dir, "checkout", self.name])
        self.repo.runner.exec(2, ["git", "-C", self.dir, "reset", "--hard", "origin/" + self.name])
        self.repo.runner.exec(2, ["git", "-C", self.dir, "submodule", "update", "--init", "--recursive"])

    def check(self) -> bool:
        current_commit = self.repo.runner.exec(2, ["git", "-C", self.dir, "rev-parse", "origin/" + self.name]).stdout
        if self.lastcommit.is_file():
            with self.lastcommit.open("rb") as f:
                last_commit = f.read()
            if last_commit == current_commit:
                self.repo.runner.log(2, "Branch " + self.name + " has not changed since last run; skipping")
                return False
        return True

    def run(self) -> None:
        self.repo.runner.log(1, f"Running tests on branch {self.name}")
        date = datetime.now()
        log_name = f"{date:%Y-%m-%d}-{date:%H%M%S}-{self.repo.name}-{self.filename}.log"

        self.info = {}
        self.repo.runner.data["branch"] = self.name
        self.repo.runner.data["branch_log"] = log_name
        self.repo.runner.save()
        t = datetime.now()
        try:
            to = parse_time(self.repo.config.get("timeout"))
            cmd = SYSTEMD_RUN_CMD + \
                ["--setenv=NIGHTLY_CONF_FILE=" + str(self.repo.runner.config_file.resolve())] + \
                ["make", "-C", str(self.dir), "nightly"]
            self.repo.runner.log(2, f"Executing {format_cmd(cmd)}")
            if not self.repo.runner.dryrun:
                with (self.repo.runner.log_dir / log_name).open("wt") as fd:
                    process = subprocess.Popen(cmd, stdout=fd, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                    self.repo.runner.data["branch_pid"] = process.pid
                    self.repo.runner.save()
                    try:
                        process.wait(timeout=to)
                        p = process.poll()
                        if p: raise subprocess.CalledProcessError(p, cmd)
                    finally:
                        self.info = self.repo.runner.load_info()
                        process.kill()
        except subprocess.TimeoutExpired as e:
            self.repo.runner.log(1, f"Run on branch {self.name} timed out after {format_time(e.timeout)}")
            failure = "timeout"
        except subprocess.CalledProcessError as e:
            self.repo.runner.log(1, f"Run on branch {self.name} failed with error code {e.returncode}")
            failure = "failure"
        else:
            self.repo.runner.log(1, f"Successfully ran on branch {self.name}")
            failure = ""

        if not self.repo.runner.dryrun:
            out = self.repo.runner.exec(1, ["git", "-C", self.dir, "rev-parse", f"origin/{self.name}"])
            with self.lastcommit.open("wb") as last_commit_fd:
                last_commit_fd.write(out.stdout)

        self.info["result"] = f"*{failure}*" if failure else "success"
        self.info["time"] = format_time((datetime.now() - t).seconds)
        self.info["file"] = log_name
        del self.repo.runner.data["branch"]
        del self.repo.runner.data["branch_log"]
        self.repo.runner.save()

if __name__ == "__main__":
    import sys
    conf_file = sys.argv[1] if len(sys.argv) > 1 else "conf/nightlies.conf"
    runner = NightlyRunner(conf_file)
    runner.load()
    runner.run()
