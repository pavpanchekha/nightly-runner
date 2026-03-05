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
        self.subrepo_dir = self.tmpdir / "subrepo"
        self.config_file = self.tmpdir / "nightlies.conf"
        self.pid_file = self.tmpdir / "running.pid"
        self.old_git_allow_protocol = os.environ.get("GIT_ALLOW_PROTOCOL")
        os.environ["GIT_ALLOW_PROTOCOL"] = "file"

        self.repos_dir.mkdir()
        self.logs_dir.mkdir()
        self.reports_dir.mkdir()

        self.git(["init", "--initial-branch=main", str(self.work_dir)], repo=self.tmpdir)
        self.git(["config", "user.name", "NR Harness"], repo=self.work_dir)
        self.git(["config", "user.email", "nr-harness@example.com"], repo=self.work_dir)
        self.commit(self.work_dir, "README.md", "seed\n", "initial main commit")

        self.git(["init", "--bare", str(self.remote_dir)], repo=self.tmpdir)
        self.git(["remote", "add", "origin", str(self.remote_dir)], repo=self.work_dir)
        self.git(["push", "origin", "main"], repo=self.work_dir)

        self.git(["init", "--initial-branch=main", str(self.subrepo_dir)], repo=self.tmpdir)
        self.git(["config", "user.name", "NR Harness"], repo=self.subrepo_dir)
        self.git(["config", "user.email", "nr-harness@example.com"], repo=self.subrepo_dir)
        self.commit(self.subrepo_dir, "sub.txt", "i am a submodule\n", "initial submodule commit")

    def tearDown(self) -> None:
        if self.old_git_allow_protocol is None:
            os.environ.pop("GIT_ALLOW_PROTOCOL", None)
        else:
            os.environ["GIT_ALLOW_PROTOCOL"] = self.old_git_allow_protocol
        shutil.rmtree(self.tmpdir)

    def test_nr_dryrun_vanilla(self) -> None:
        first = self.nightly(repo_updates={}, complete=True)
        self.assertEqual(first, ["main"])

        second = self.nightly()
        self.assertEqual(second, [])

        self.create_branch("feature/test", "feature.txt", "v1\n", "initial feature commit")

        third = self.nightly()
        self.assertEqual(third, ["feature/test"])

    def test_nr_dryrun_baseline(self) -> None:
        first = self.nightly(repo_updates={"baseline": "main"}, complete=True)
        self.assertEqual(first, ["main"])

        second = self.nightly()
        self.assertEqual(second, [])

        self.create_branch("feature/test", "feature.txt", "v1\n", "initial feature commit")

        third = self.nightly()
        self.assertEqual(third, ["main", "feature/test"])

    def test_brand_new_repo_creates_checkout_and_worktrees(self) -> None:
        ran = self.nightly(repo_updates={})
        self.assertEqual(ran, ["main"])

        repo_dir = self.repos_dir / "testrepo"
        self.assertTrue((repo_dir / ".checkout").is_dir())
        self.assertTrue((repo_dir / "main").is_dir())
        self.assertFalse((repo_dir / "feature_2ftest").exists())

    def test_adding_new_branch_gets_scheduled(self) -> None:
        self.nightly(repo_updates={}, complete=True)

        self.create_branch("hotfix/new", "hotfix.txt", "v1\n", "add hotfix branch")

        ran = self.nightly()
        self.assertEqual(ran, ["hotfix/new"])

    def test_deleting_branch_removes_worktree_directory(self) -> None:
        self.create_branch("feature/test", "feature.txt", "v1\n", "initial feature commit")
        self.nightly(repo_updates={}, complete=True)

        repo_dir = self.repos_dir / "testrepo"
        deleted_branch_dir = repo_dir / "feature_2ftest"
        self.assertTrue(deleted_branch_dir.is_dir())

        self.git(["branch", "-D", "feature/test"], repo=self.remote_dir)
        ran = self.nightly()

        self.assertEqual(ran, [])
        self.assertFalse(deleted_branch_dir.exists())

    def test_commits_only_schedule_changed_branches(self) -> None:
        self.create_branch("feature/test", "feature.txt", "v1\n", "initial feature commit")
        self.nightly(repo_updates={}, complete=True)

        self.git(["checkout", "main"], repo=self.work_dir)
        self.commit(self.work_dir, "README.md", "seed\nmain v2\n", "update main")
        self.git(["push", "origin", "main"], repo=self.work_dir)

        ran_main = self.nightly()
        self.assertEqual(ran_main, ["main"])
        self.nightly(complete=True)

        self.git(["checkout", "feature/test"], repo=self.work_dir)
        self.commit(self.work_dir, "feature.txt", "v4\n", "update feature")
        self.git(["push", "origin", "feature/test"], repo=self.work_dir)
        self.git(["checkout", "main"], repo=self.work_dir)

        ran_feature = self.nightly()
        self.assertEqual(ran_feature, ["feature/test"])

    def test_runner_publishes_reports(self) -> None:
        makefile = "nightly:\n\t@printf '<h1>ok</h1>\\n' > report/index.html\n"
        (self.work_dir / "Makefile").write_text(makefile)
        self.git(["add", "Makefile"], repo=self.work_dir)
        self.git(["commit", "-m", "add report-producing nightly target"], repo=self.work_dir)
        self.git(["push", "origin", "main"], repo=self.work_dir)

        self.nightly(
            repo_updates={"branches": "main", "report": "report"},
            complete=True,
        )
        result = self.run_runner("main", "publish-report.log")

        self.assertEqual(result.returncode, 0, msg=result.stdout + "\n" + result.stderr)

        published = list((self.reports_dir / "testrepo").glob("*/index.html"))
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].read_text(), "<h1>ok</h1>\n")
        self.assertFalse((self.repos_dir / "testrepo" / "main" / "report").exists())

    def test_runner_handles_invalid_report_destination(self) -> None:
        invalid_reports = self.tmpdir / "reports-file"
        invalid_reports.write_text("not a directory\n")

        makefile = "nightly:\n\t@printf 'broken publish path\\n' > report/index.txt\n"
        (self.work_dir / "Makefile").write_text(makefile)
        self.git(["add", "Makefile"], repo=self.work_dir)
        self.git(["commit", "-m", "add nightly report for invalid destination case"], repo=self.work_dir)
        self.git(["push", "origin", "main"], repo=self.work_dir)

        self.nightly(
            repo_updates={"branches": "main", "report": "report"},
            default_updates={"reports": str(invalid_reports)},
            complete=True,
        )
        result = self.run_runner("main", "invalid-report.log")

        self.assertEqual(result.returncode, 0, msg=result.stdout + "\n" + result.stderr)
        self.assertIn("Error saving report for `main`", result.stdout)
        self.assertTrue((self.repos_dir / "testrepo" / "main" / "report" / "index.txt").exists())

    def test_submodule_regression(self) -> None:
        # Initial NR checkout/worktree setup without submodules.
        self.nightly(repo_updates={"branches": "main"}, complete=True)

        self.git(["checkout", "main"], repo=self.work_dir)
        makefile = "nightly:\n\t@echo nightly-ok\n"
        (self.work_dir / "Makefile").write_text(makefile)
        self.git(["add", "Makefile"], repo=self.work_dir)
        self.git(
            [
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(self.subrepo_dir),
                "sub1",
            ],
            repo=self.work_dir,
        )
        self.git(["commit", "-m", "add local submodule"], repo=self.work_dir)
        self.git(["push", "origin", "main"], repo=self.work_dir)

        # This mirrors the repro's post-submodule-add fetch path.
        self.assertEqual(self.nightly(), ["main"])

        # This mirrors the repro's reset+submodule-update in a worktree path.
        result = self.run_runner("main", "submodule-regression.log")

        if result.returncode != 0:
            raise RuntimeError(
                f"runner.py failed:\nstdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )

    def write_config(
        self,
        repo_updates: dict[str, str],
        default_updates: dict[str, str] | None = None,
    ) -> None:
        conf = configparser.ConfigParser()
        defaults = {
            "repos": str(self.repos_dir),
            "logs": str(self.logs_dir),
            "reports": str(self.reports_dir),
            "baseurl": "https://nightlies.example/",
            "pid": str(self.pid_file),
            "dryrun": "1",
        }
        if default_updates:
            defaults.update(default_updates)
        conf["DEFAULT"] = defaults
        repo_config = {"url": str(self.remote_dir)}
        repo_config.update(repo_updates)
        conf["testrepo"] = repo_config
        with self.config_file.open("w") as f:
            conf.write(f)

    def nightly(
        self,
        repo_updates: dict[str, str] | None = None,
        default_updates: dict[str, str] | None = None,
        complete: bool = False,
    ) -> list[str]:
        if repo_updates is not None or default_updates is not None or not self.config_file.exists():
            self.write_config(repo_updates or {}, default_updates)

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

    def create_branch(self, branch: str, filename: str, contents: str, message: str) -> None:
        self.git(["checkout", "-b", branch], repo=self.work_dir)
        self.commit(self.work_dir, filename, contents, message)
        self.git(["push", "origin", branch], repo=self.work_dir)
        self.git(["checkout", "main"], repo=self.work_dir)

    def commit(self, repo: Path, filename: str, contents: str, message: str) -> None:
        file = repo / filename
        file.write_text(contents)
        self.git(["add", filename], repo=repo)
        self.git(["commit", "-m", message], repo=repo)

    def git(self, cmd: list[str], repo: Path) -> None:
        full_cmd = ["git", "-C", str(repo)] + cmd
        result = subprocess.run(full_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"git command failed: {' '.join(full_cmd)}\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )


if __name__ == "__main__":
    unittest.main()
