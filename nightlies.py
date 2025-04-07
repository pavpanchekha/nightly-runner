#!/usr/bin/env python3

from typing import Any, List, Dict, Union, Optional, cast, Sequence
import os, sys, subprocess, time
from datetime import datetime
from pathlib import Path
import configparser
import json
import shlex, shutil
import slack, apt
import urllib.request

def format_time(ts : float) -> str:
    t = float(ts)
    if t < 120:
        return f"{t:.1f}s"
    elif t < 120*60:
        return f"{t/60:.1f}m"
    else:
        return f"{t/60/60:.1f}h"
    
def format_cmd(s : Sequence[Union[str, Path]]) -> str:
    if hasattr(shlex, "join"):
        return shlex.join([str(part) for part in s])
    else: # Compatibility with old Python 3
        return " ".join([
            str(part) if " " not in str(part) else '"{}"'.format(part)
            for part in s
        ])

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

def repo_to_url(repo : str) -> str:
    if repo and ":" in repo: return repo
    return "git@github.com:" + repo + ".git"

def copything(src : Path, dst : Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)

SYSTEMD_SLICE = "nightlies.slice"

SYSTEMD_RUN_CMD = [
    "sudo", # There might not be a user session manager, so run using root's
    "systemd-run",
    "--collect", # If it fails, throw it away
    "--wait", # Wait for it to finish
    "--pty", # Pass through stdio
    "--pipe", # Pass through file descriptors
    f"--uid={os.getuid()}", # As the current user
    f"--gid={os.getgid()}", # As the current group
    f"--slice={SYSTEMD_SLICE}", # Run with the nightly resource limits
    "--property=Delegate=yes", # Spawned Docker instances inherit resource limits
    "--service-type=simple", # It just execs a program
]

REPO_BADGES = [
    "baseline", # Run this branch if you run anything else
    "always", # Run this branch every day
    "never", # Never run this branch
]

class NightlyRunner:
    def __init__(self, config_file : str) -> None:
        self.config_file = Path(config_file)
        self.self_dir = Path(__file__).resolve().parent
        self.data : Any = None

    def update_system_repo(self, dir : str, repo : str, branch : str) -> None:
        if not Path(dir).is_dir():
            self.log(1, f"Downloading system {repo} repository {dir}")
            self.exec(2, ["git", "-C", dir, "clone", "--recursive",
                          "--branch", branch, "--", repo_to_url(repo), dir])
            self.restart()

        self.log(1, f"Updating system {repo} repository in {dir}")
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
        else:
            self.log(1, f"System {dir} repository updated; will need to restart")
            self.restart()

    def load(self) -> None:
        assert self.config_file.is_file(), f"Configuration file {self.config_file} is not a file"
        self.config = configparser.ConfigParser()
        self.config.read(str(self.config_file))
        self.repos : List[Repository] = []

        defaults = self.config.defaults()
        self.base_url = defaults.get("baseurl")
        if self.base_url and not self.base_url.endswith("/"): self.base_url += "/"
        self.dir = Path(defaults.get("repos", ".")).resolve()
        self.log_dir = Path(defaults.get("logs", "logs")).resolve()
        self.dryrun = "dryrun" in defaults
        self.pid_file = Path(defaults.get("pid", "running.pid")).resolve()
        self.info_file = Path(defaults.get("info", "running.info")).resolve()
        self.config_file = Path(defaults.get("conffile", str(self.config_file))).resolve()
        self.report_dir = Path(defaults.get("reports", "reports")).resolve()

        self.secrets = configparser.ConfigParser()
        if defaults.get("secrets"):
            for file in Path(defaults["secrets"]).iterdir():
                if not file.name.endswith(".conf"): continue
                with file.open() as f:
                    self.secrets.read_file(f, source=f.name)

        for name in self.config.sections():
            self.repos.append(Repository(self, name, self.config[name]))

    def update(self) -> None:
        if not self.config.getboolean("DEFAULT", "update", fallback=False): return

        runner_repo = self.config.defaults().get("self", "pavpanchekha/nightly-runner")
        runner_branch = self.config.defaults().get("selfbranch", "main")
        self.update_system_repo(".", runner_repo, runner_branch)

        conf_dir = os.path.dirname(self.config_file)
        conf_repo = self.config.defaults()["conf"]
        conf_branch = self.config.defaults().get("confbranch", "main")
        self.update_system_repo(conf_dir, conf_repo, conf_branch)

        sec_repo = self.config.defaults()["secrets"]
        sec_branch = self.config.defaults().get("secretsbranch", "main")
        self.update_system_repo("secrets", sec_repo, sec_branch)

    def restart(self) -> None:
        self.log(0, "Restarting nightly run due to updated system repositories")
        os.execv(sys.executable, ["python3"] + sys.argv)

    def exec(self, level : int, cmd : Sequence[Union[str, Path]]) -> subprocess.CompletedProcess[bytes]:
        self.log(level, f"Executing {format_cmd(cmd)}")
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)

    def log(self, level : int, s : str) -> None:
        with self.log_path.open("at") as f:
            f.write("{}\t{}{}\n".format(datetime.now() - self.start, "    " * level, s))

    def save(self) -> None:
        with self.pid_file.open("w") as f:
            json.dump(self.data, f)

    def add_info(self, cmd : str, *args : str) -> None:
        data = format_cmd([cmd] + list(args))
        self.log(3, f"Adding info {data}")
        with self.info_file.open("a") as f:
            f.write(data + "\n")

    def load_info(self) -> Dict[str, str]:
        out : Dict[str, str] = {}
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

    def load_pid(self) -> None:
        try:
            with self.pid_file.open("r") as f:
                self.data = json.load(f)
            self.log_path = Path(cast(str, self.data["log"]))
            self.start = datetime.fromisoformat(cast(str, self.data["start"]))
        except OSError:
            return

    def try_lock(self) -> bool:
        try:
            self.pid_file.touch(exist_ok=False)
            return True
        except FileExistsError:
            return False

    def lock(self) -> bool:
        if self.try_lock():
            return True
        else:
            try:
                with self.pid_file.open("r") as f:
                    current_process = json.load(f)
                    self.log(0, f"Nightly already running on pid {current_process['pid']}")
            except (OSError, json.decoder.JSONDecodeError):
                self.log(0, f"Nightly already running")

            if self.config.getboolean("DEFAULT", "wait", fallback=True):
                while True:
                    if self.try_lock():
                        return True
                    else:
                        self.log(1, f"Sleeping for 15 minutes...")
                        time.sleep(15 * 60)
            else:
                return False


    def run(self) -> None:
        self.start = datetime.now()
        name = f"{self.start:%Y-%m-%d}-{self.start:%H%M%S}.log"
        self.log_path = self.log_dir / name
        self.log(0, f"Nightly script starting up at {self.start:%H:%M}")
        self.log(0, f"Loaded configuration file {self.config_file}")
        self.update()

        if not self.lock():
            return

        self.data = {
            "pid": os.getpid(),
            "start": self.start.isoformat(),
            "config": str(Path(self.config_file).resolve()),
            "log": str(self.log_path),
        }
        self.save()

        if self.dryrun:
            self.log(0, "Running in dry-run mode. No nightlies will be executed.")

        if Path(".git/info").exists():
            with open(".git/info/exclude", "wt") as f:
                for repo in self.repos:
                    f.write(repo.dir.name + "\n")

        plan : List[Branch] = []
        for repo in self.repos:
            try:
                self.data["repo"] = repo.name
                self.save()
                repo.load()
                repo.plan()
                plan.extend(repo.runnable)
            except subprocess.CalledProcessError as e:
                repo.fatalerror = f"Process {format_cmd(e.cmd)} returned error code {e.returncode}"
                self.log(1, repo.fatalerror)
                repo.post()
            except OSError as e:
                repo.fatalerror = f"Fatal error: {str(e)}"
                self.log(1, repo.fatalerror)
                repo.post()
            finally:
                del self.data["repo"]

        for i, branch in enumerate(plan):
            if i and not self.dryrun:
                self.log(1, f"Waiting for machine to cool down")
                time.sleep(30) # To avoid thermal throttling

            try:
                self.data["repo"] = branch.repo.name
                self.data["branch"] = branch.name
                self.data["runs_done"] = i
                self.data["runs_total"] = len(plan)
                self.save()

                branch.run()
            except subprocess.CalledProcessError as e:
                branch.repo.fatalerror = f"Process {format_cmd(e.cmd)} returned error code {e.returncode}"
                self.log(1, branch.repo.fatalerror)
            finally:
                if all([idx <= i for idx, b in enumerate(plan) if branch.repo == b.repo]):
                    self.log(0, f"Finished nightly run for {branch.repo.name}")
                    branch.repo.post()

                del self.data["repo"]
                del self.data["branch"]
                # del self.data["runs_done"]
                # del self.data["runs_total"]
                self.save()

        self.pid_file.unlink()
        self.log(0, "Finished nightly run for today")

class Repository:
    def __init__(self, runner : NightlyRunner, name : str, configuration : configparser.SectionProxy):
        self.runner = runner
        self.config = configuration
        self.runnable : List[Branch] = []

        self.slack_channel : Optional[str] = configuration.get("slack")
        if self.slack_channel:
            self.slack_token : Optional[str] = self.runner.secrets[self.slack_channel]["slack"]
        else:
            self.slack_token = None
        self.run_all = False

        self.url = repo_to_url(self.config.get("url", configuration.get("github", name)))

        self.name = name.split("/")[-1]
        self.dir = runner.dir / self.name
        self.checkout = self.dir / ".checkout"
        self.status = self.dir / ".status"
        self.ignored_files = {
            self.dir / path
            for path in shlex.split(self.config.get("ignore", ""))
        } | set([self.checkout, self.status])
        self.report_dir_name = configuration.get("report")
        self.image_file_name = configuration.get("image")
        
        self.branches : Dict[str, Branch] = {}
        self.fatalerror: Optional[str] = None

    def list_branches(self) -> List[str]:
        git_branch = self.runner.exec(2, ["git", "-C", self.checkout, "branch", "-r"])
        return [
            branch.split("/", 1)[-1] for branch
            in git_branch.stdout.decode("utf8").strip().split("\n")
            if "->" not in branch
        ]

    def list_pr_branches(self) -> Dict[str, int]:
        if self.url.startswith("git@github.com:") and self.url.endswith(".git"):
            gh_name = self.url[len("git@github.com:"):-len(".git")]
            pulls_url = f"https://api.github.com/repos/{gh_name}/pulls"
            with urllib.request.urlopen(pulls_url) as data:
                pr_data = json.load(data)
            out : Dict[str, int] = {}
            for pr in pr_data:
                if pr["head"]["repo"]["full_name"] == gh_name:
                    out[pr["head"]["ref"]] = pr["number"]
            return out
        else:
            return {}

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

        if not self.checkout.is_dir():
            self.runner.log(1, "Checking out base repository for " + self.name)
            self.runner.exec(2, ["git", "clone", "--recursive", self.url, self.checkout])
            self.runner.exec(2, ["git", "-C", self.checkout, "checkout", "--detach"])

        self.runner.log(1, "Updating branches for " + self.name)
        self.runner.exec(2, ["git", "-C", self.checkout, "fetch", "origin", "--prune", "--recurse-submodules"])
        self.runner.exec(2, ["git", "-C", self.checkout, "submodule", "update", "--init", "--recursive"])

        if "branches" in self.config:
            all_branches = self.config["branches"].split()
        else:
            all_branches = self.list_branches()
        self.branches = { branch: Branch(self, branch) for branch in all_branches }

        if self.config.getboolean("clean", fallback=True):
            self.clean()

        for branch in self.branches.values():
            branch.load()

        self.assign_badges()

    def clean(self) -> None:
        expected_files = self.ignored_files.union(*[
            {b.dir, b.lastcommit} for b in self.branches.values()
        ])
        self.runner.log(1, "Cleaning unnecessary files")
        for fn in self.dir.iterdir():
            if fn not in expected_files:
                self.runner.log(2, f"Deleting unknown file {fn}")
                if not self.runner.dryrun:
                    if fn.is_dir():
                        shutil.rmtree(str(fn))
                    else:
                        fn.unlink()
        self.runner.exec(2, ["git", "-C", self.checkout, "worktree", "prune"])

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

    def plan(self) -> None:
        if self.run_all:
            self.runnable = list(self.branches.values())
            return

        self.runner.log(1, "Filtering branches " + ", ".join(self.branches))
        self.runnable = [branch for branch in self.branches.values() if branch.plan()]
        for branch in self.branches.values():
            if "always" in branch.badges and branch not in self.runnable:
                self.runner.log(2, f"Adding always run on branch {branch.name}")
                self.runnable.append(branch)
            if "baseline" in branch.badges and self.runnable:
                self.runner.log(2, f"Adding baseline branch {branch.name}")
                if branch in self.runnable:
                    self.runnable.remove(branch)
                self.runnable.insert(0, branch)
            if "never" in branch.badges and branch in self.runnable:
                self.runner.log(2, f"Removing never run on branch {branch.name}")
                self.runnable.remove(branch)
        if self.runnable:
            self.runner.log(1, "Found runnable branches " + ", ".join([b.name for b in self.runnable]))
        else:
            self.runner.log(1, "No runnable branches for " + self.name)

    def post(self) -> None:
        if not self.slack_token:
            return self.runner.log(1, f"Not posting to Slack, slack not configured")
        elif not self.runner.base_url:
            return self.runner.log(1, f"Not posting to Slack, baseurl not configured")
        elif not hasattr(self, "runnable"):
            return self.runner.log(1, f"Not posting to Slack, some kind of error occurred")
        else:
            runner.log(1, f"Posting results of run to slack!")

        if self.fatalerror:
            data = slack.build_fatal(self.name, self.fatalerror, self.runner.base_url)
        else:
            runs = { branch.name : branch.info for branch in self.runnable if branch.info }
            if not runs: return
            data = slack.build_runs(self.name, runs, self.runner.base_url)

        if self.run_all:
            apt.post(data)

        if not self.runner.dryrun or self.fatalerror:
            slack.send(self.runner, self.slack_token, data)

class Branch:
    def __init__(self, repo : Repository, name : str):
        self.repo = repo
        self.name = name
        self.filename = Branch.escape_filename(self.name)
        self.dir = self.repo.dir / self.filename
        self.lastcommit = self.repo.dir / (self.filename + ".last-commit")
        self.badges : List[str] = []
        self.info : Dict[str, str] = {}

        self.report_dir = self.dir / self.repo.report_dir_name if self.repo.report_dir_name else None
        self.image_file = self.report_dir / self.repo.image_file_name if self.report_dir and self.repo.image_file_name else None

    def last_run(self) -> float:
        try:
            return os.path.getmtime(str(self.lastcommit))
        except FileNotFoundError:
            return float("inf")

    @staticmethod
    def parse_filename(filename : str) -> str:
        return filename.replace("%2f", "/").replace("%25", "%")

    @staticmethod
    def escape_filename(filename : str) -> str:
        return filename.replace("%", "%25").replace("/", "%2f")

    def load(self) -> None:
        if not self.dir.is_dir():
            relpath = self.dir.relative_to(self.repo.dir)
            self.repo.runner.exec(2, ["git", "-C", self.repo.checkout, "worktree", "add", ".." / relpath, self.name])
        self.repo.runner.exec(2, ["git", "-C", self.dir, "submodule", "update", "--init", "--recursive"])
        self.repo.runner.exec(2, ["git", "-C", self.dir, "reset", "--hard", "--recurse-submodules", "origin/" + self.name])

    def plan(self) -> bool:
        self.current_commit = self.repo.runner.exec(2, ["git", "-C", self.dir, "rev-parse", "origin/" + self.name]).stdout
        if self.lastcommit.is_file():
            with self.lastcommit.open("rb") as f:
                last_commit = f.read()
            if last_commit == self.current_commit:
                self.repo.runner.log(2, "Branch " + self.name + " has not changed since last run; skipping")
                return False
        return True

    def run(self) -> None:
        self.repo.runner.log(0, f"Running branch {self.name} on repo {self.repo.name}")
        date = datetime.now()
        log_name = f"{date:%Y-%m-%d}-{date:%H%M%S}-{self.repo.name}-{self.filename}.log"

        self.repo.runner.data["branch_log"] = log_name
        self.repo.runner.save()

        t = datetime.now()
        try:
            to = parse_time(self.repo.config.get("timeout"))
            env_path = str(self.repo.runner.self_dir) + ":" + cast(str, os.getenv('PATH'))
            cmd = SYSTEMD_RUN_CMD + \
                ["--setenv=NIGHTLY_CONF_FILE=" + str(self.repo.runner.config_file.resolve())] + \
                [f"--setenv=PATH={env_path}", "make", "-C", str(self.dir), "nightly"]
            self.repo.runner.log(1, f"Executing {format_cmd(cmd)}")
            if not self.repo.runner.dryrun:
                if self.report_dir:
                    if self.report_dir.exists():
                        shutil.rmtree(self.report_dir, ignore_errors=True)
                    self.report_dir.mkdir(exist_ok=True)

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
                        self.repo.runner.exec(2, ["sudo", "systemctl", "stop", "nightlies.slice"])

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
            out = self.repo.runner.exec(2, ["git", "-C", self.dir, "rev-parse", f"origin/{self.name}"])
            with self.lastcommit.open("wb") as last_commit_fd:
                last_commit_fd.write(out.stdout)

            # Auto-publish report if configured
            if self.report_dir and self.report_dir.exists() and "url" not in self.info:
                assert self.repo.runner.base_url, f"Cannot publish, no baseurl configured"
                name = f"{int(time.time())}:{self.filename}:{out.stdout[:8].decode('ascii')}"
                dest_dir = self.repo.runner.report_dir / self.repo.name / name

                if self.report_dir.exists() and not dest_dir.exists():
                    self.repo.runner.log(2, f"Publishing report directory {self.report_dir} to {dest_dir}")
                    copything(self.report_dir, dest_dir)
                    url_base = self.repo.runner.base_url + "reports/" + self.repo.name + "/" + name
                    self.info["url"] = url_base
                    if self.image_file and self.image_file.exists():
                        self.repo.runner.log(2, f"Linking image file {self.image_file}")
                        path = self.image_file.relative_to(self.report_dir)
                        self.info["img"] = url_base + "/" + str(path)
                elif dest_dir.exists():
                    self.repo.runner.log(2, f"Destination directory {dest_dir} already exists, skipping")
                else:
                    self.repo.runner.log(2, f"Report directory {self.report_dir} does not exist")


        self.info["result"] = f"*{failure}*" if failure else "success"
        self.info["time"] = format_time((datetime.now() - t).seconds)
        self.info["file"] = log_name
        del self.repo.runner.data["branch_log"]
        self.repo.runner.save()

if __name__ == "__main__":
    import sys
    conf_file = sys.argv[1] if len(sys.argv) > 1 else "conf/nightlies.conf"
    runner = NightlyRunner(conf_file)
    runner.load()
    runner.run()
