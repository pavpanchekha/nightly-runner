#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import configparser
import os
import shutil
import subprocess
import tempfile
import unittest

from nightlies import NightlyRunner


class TestNightlyRunnerHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="nr-harness-"))
        self.repos_dir = self.tmpdir / "repos"
        self.logs_dir = self.tmpdir / "logs"
        self.reports_dir = self.tmpdir / "reports"
        self.work_dir = self.tmpdir / "work"
        self.remote_dir = self.tmpdir / "origin.git"
        self.config_file = self.tmpdir / "nightlies.conf"
        self.pid_file = self.tmpdir / "running.pid"
        self.old_git_allow_protocol = os.environ.get("GIT_ALLOW_PROTOCOL")
        os.environ["GIT_ALLOW_PROTOCOL"] = "file"

        self.repos_dir.mkdir()
        self.logs_dir.mkdir()
        self.reports_dir.mkdir()

        self.git(["init", "--initial-branch=main", str(self.work_dir)])
        self.git(["-C", str(self.work_dir), "config", "user.name", "NR Harness"])
        self.git(["-C", str(self.work_dir), "config", "user.email", "nr-harness@example.com"])
        self.commit(self.work_dir, "README.md", "seed\n", "initial main commit")

        self.git(["-C", str(self.work_dir), "checkout", "-b", "feature/test"])
        self.commit(self.work_dir, "feature.txt", "v1\n", "initial feature commit")
        self.git(["-C", str(self.work_dir), "checkout", "main"])

        self.git(["init", "--bare", str(self.remote_dir)])
        self.git(["-C", str(self.work_dir), "remote", "add", "origin", str(self.remote_dir)])
        self.git(["-C", str(self.work_dir), "push", "--all", "origin"])

    def tearDown(self) -> None:
        if self.old_git_allow_protocol is None:
            os.environ.pop("GIT_ALLOW_PROTOCOL", None)
        else:
            os.environ["GIT_ALLOW_PROTOCOL"] = self.old_git_allow_protocol
        shutil.rmtree(self.tmpdir)

    def test_nr_dryrun_vanilla(self) -> None:
        self.write_config({})

        first = self.nightly(complete=True)
        self.assertCountEqual(first, ["main", "feature/test"])

        second = self.nightly()
        self.assertEqual(second, [])

        self.git(["-C", str(self.work_dir), "checkout", "feature/test"])
        self.commit(self.work_dir, "feature.txt", "v2\n", "update feature commit")
        self.git(["-C", str(self.work_dir), "push", "origin", "feature/test"])
        self.git(["-C", str(self.work_dir), "checkout", "main"])

        third = self.nightly()
        self.assertEqual(third, ["feature/test"])

    def test_nr_dryrun_baseline(self) -> None:
        self.write_config({"baseline": "main"})

        first = self.nightly(complete=True)
        self.assertCountEqual(first, ["main", "feature/test"])

        second = self.nightly()
        self.assertEqual(second, [])

        self.git(["-C", str(self.work_dir), "checkout", "feature/test"])
        self.commit(self.work_dir, "feature.txt", "v3\n", "update feature commit for baseline")
        self.git(["-C", str(self.work_dir), "push", "origin", "feature/test"])
        self.git(["-C", str(self.work_dir), "checkout", "main"])

        third = self.nightly()
        self.assertEqual(third, ["main", "feature/test"])

    def test_submodule_regression(self) -> None:
        self.write_config({"branches": "main"})

        # Initial NR checkout/worktree setup without submodules.
        self.nightly(complete=True)

        subrepo_dir = self.tmpdir / "subrepo"
        self.git(["init", "--initial-branch=main", str(subrepo_dir)])
        self.git(["-C", str(subrepo_dir), "config", "user.name", "NR Harness"])
        self.git(["-C", str(subrepo_dir), "config", "user.email", "nr-harness@example.com"])
        self.commit(subrepo_dir, "sub.txt", "i am a submodule\n", "initial submodule commit")

        self.git(["-C", str(self.work_dir), "checkout", "main"])
        makefile = "nightly:\n\t@echo nightly-ok\n"
        (self.work_dir / "Makefile").write_text(makefile)
        self.git(["-C", str(self.work_dir), "add", "Makefile"])
        self.git(
            [
                "-C",
                str(self.work_dir),
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(subrepo_dir),
                "sub1",
            ]
        )
        self.git(["-C", str(self.work_dir), "commit", "-m", "add local submodule"])
        self.git(["-C", str(self.work_dir), "push", "origin", "main"])

        # This mirrors the repro's post-submodule-add fetch path.
        self.assertEqual(self.nightly(), ["main"])

        # This mirrors the repro's reset+submodule-update in a worktree path.
        result = self.run_runner("main", "submodule-regression.log")

        if result.returncode != 0:
            raise RuntimeError(
                f"runner.py failed:\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )

    def write_config(self, repo_updates: dict[str, str]) -> None:
        conf = configparser.ConfigParser()
        conf["DEFAULT"] = {
            "repos": str(self.repos_dir),
            "logs": str(self.logs_dir),
            "reports": str(self.reports_dir),
            "pid": str(self.pid_file),
            "dryrun": "1",
        }
        repo_config = {"url": str(self.remote_dir)}
        repo_config.update(repo_updates)
        conf["testrepo"] = repo_config
        with self.config_file.open("w") as f:
            conf.write(f)

    def nightly(self, complete: bool = False) -> list[str]:
        runner = NightlyRunner(str(self.config_file))
        runner.load()
        runner.run()
        repo = runner.repos[0]
        ran = [branch.name for branch in repo.runnable]
        if complete:
            for branch in repo.runnable:
                branch.config["commit"] = branch.current_commit
                branch.save_metadata()
        return ran

    def run_runner(self, branch: str, log_name: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "runner.py", str(self.config_file), "testrepo", branch, log_name],
            cwd=str(Path(__file__).resolve().parent),
            capture_output=True,
            text=True,
        )

    def commit(self, repo: Path, filename: str, contents: str, message: str) -> None:
        file = repo / filename
        file.write_text(contents)
        self.git(["-C", str(repo), "add", filename])
        self.git(["-C", str(repo), "commit", "-m", message])

    def git(self, cmd: list[str]) -> None:
        result = subprocess.run(["git"] + cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"git command failed: {' '.join(['git'] + cmd)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
