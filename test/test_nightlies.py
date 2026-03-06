#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import configparser
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

# Ensure direct execution (uv run test/test_nightlies.py) resolves project modules from repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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

    def with_cwd(self, path: Path) -> tuple[str, Path]:
        old = os.getcwd()
        os.chdir(path)
        return old, path

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
        first = self.nightly(repo_updates={}, complete=True)
        self.dump_repo_state("after-initial-complete")
        self.assertEqual(
            first,
            ["main"],
            msg=(
                f"Unexpected initial runnable branches: {first}\n"
                f"{self.format_repo_state('after-initial-complete-assert')}"
            ),
        )

        self.create_branch("hotfix/new", "hotfix.txt", "v1\n", "add hotfix branch")
        self.dump_repo_state("after-hotfix-push-before-nightly")

        ran = self.nightly()
        self.dump_repo_state("after-hotfix-nightly")
        self.assertEqual(
            ran,
            ["hotfix/new"],
            msg=(
                f"Unexpected runnable branches: {ran}\n"
                f"{self.format_repo_state('assertion-context')}"
            ),
        )

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
        self.makefile(
            "add report-producing nightly target",
            ["printf '<h1>ok</h1>\\n' > report/index.html"],
        )
        self.git(["push", "origin", "main"], repo=self.work_dir)

        self.nightly(
            repo_updates={"branches": "main", "report": "report"},
            complete=True,
        )
        result = self.run_runner("main", "publish-report.log")
        report_dir = self.published_report(result)
        published = report_dir / "index.html"
        self.assertTrue(published.exists())
        self.assertEqual(published.read_text(), "<h1>ok</h1>\n")
        # Published reports should be moved out of the worktree so later runs start clean.
        self.assertFalse((self.repos_dir / "testrepo" / "main" / "report").exists())

    def test_runner_handles_invalid_report_destination(self) -> None:
        invalid_reports = self.tmpdir / "reports-file"
        invalid_reports.write_text("not a directory\n")

        self.makefile(
            "add nightly report for invalid destination case",
            ["printf 'broken publish path\\n' > report/index.txt"],
        )
        self.git(["push", "origin", "main"], repo=self.work_dir)

        self.nightly(
            repo_updates={"branches": "main", "report": "report"},
            default_updates={"reports": str(invalid_reports)},
            complete=True,
        )
        result = self.run_runner("main", "invalid-report.log")
        self.assertIn("Error saving report for `main`", result.stdout)
        self.assertTrue((self.repos_dir / "testrepo" / "main" / "report" / "index.txt").exists())

    def test_runner_publishes_nested_report_directory(self) -> None:
        self.makefile(
            "add nested report-producing nightly target",
            [
                "mkdir -p nested/out/report",
                "printf '<h1>nested</h1>\\n' > nested/out/report/index.html",
            ],
        )
        self.git(["push", "origin", "main"], repo=self.work_dir)

        self.nightly(
            repo_updates={"branches": "main", "report": "nested/out/report"},
            complete=True,
        )
        result = self.run_runner("main", "publish-nested-report.log")
        report_dir = self.published_report(result)
        published = report_dir / "index.html"
        self.assertTrue(published.exists())
        self.assertEqual(published.read_text(), "<h1>nested</h1>\n")

    def test_runner_tracks_remote_feature_branch_tip(self) -> None:
        self.create_branch(
            "feature/test",
            "Makefile",
            self.makefile_text(["printf '<h1>feature-v1</h1>\\n' > report/index.html"]),
            "feature v1",
        )

        self.nightly(
            repo_updates={"branches": "feature/test", "report": "report"},
            complete=True,
        )
        first = self.run_runner("feature/test", "feature-tip-v1.log")
        first_dir = self.published_report(first)
        self.assertEqual((first_dir / "index.html").read_text(), "<h1>feature-v1</h1>\n")

        self.git(["checkout", "feature/test"], repo=self.work_dir)
        self.commit(
            self.work_dir,
            "Makefile",
            self.makefile_text(["printf '<h1>feature-v2</h1>\\n' > report/index.html"]),
            "feature v2",
        )
        self.git(["push", "origin", "feature/test"], repo=self.work_dir)
        self.git(["checkout", "main"], repo=self.work_dir)

        # Refresh origin refs in the branch worktree before rerunning branch runner.
        self.nightly(repo_updates={"branches": "feature/test", "report": "report"})
        second = self.run_runner("feature/test", "feature-tip-v2.log")
        second_dir = self.published_report(second)
        self.assertEqual((second_dir / "index.html").read_text(), "<h1>feature-v2</h1>\n")

    def test_submodule_regression(self) -> None:
        # Initial NR checkout/worktree setup without submodules.
        self.nightly(repo_updates={"branches": "main"}, complete=True)

        self.git(["checkout", "main"], repo=self.work_dir)
        (self.work_dir / "Makefile").write_text(self.makefile_text(["echo nightly-ok"]))
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
        self.run_runner("main", "submodule-regression.log")

    def test_nightly_clears_transient_runner_state(self) -> None:
        self.write_config(repo_updates={})
        runner = NightlyRunner(str(self.config_file))
        runner.load()
        old_cwd, _ = self.with_cwd(self.tmpdir)
        try:
            runner.run()
        finally:
            os.chdir(old_cwd)
        self.assertNotIn("repo", runner.data)
        self.assertIn("pid", runner.data)
        self.assertIn("log", runner.data)

    def test_nightly_writes_log_under_configured_logs_dir(self) -> None:
        self.nightly(repo_updates={})
        logs = sorted(self.logs_dir.glob("*.log"))
        self.assertGreaterEqual(len(logs), 1)
        self.assertTrue(all(p.parent == self.logs_dir for p in logs))

    def test_clean_removes_unknown_files_but_keeps_branch_metadata(self) -> None:
        self.create_branch("feature/test", "feature.txt", "v1\n", "initial feature commit")
        self.nightly(repo_updates={}, complete=True)
        repo_dir = self.repos_dir / "testrepo"
        junk_dir = repo_dir / "junk-dir"
        junk_file = repo_dir / "junk.txt"
        junk_dir.mkdir()
        junk_file.write_text("junk\n")

        self.nightly(repo_updates={})

        self.assertFalse(junk_dir.exists())
        # Dry-run keeps unknown files but should still remove unknown directories.
        self.assertTrue(junk_file.exists())
        self.assertTrue((repo_dir / "main.json").exists())
        self.assertTrue((repo_dir / "feature_2ftest.json").exists())

    def test_runner_timeout_returns_failure_and_writes_metadata(self) -> None:
        self.makefile(
            "add timeout nightly target",
            [
                "sleep 1",
                "printf '<h1>late</h1>\\n' > report/index.html",
            ],
        )
        self.git(["push", "origin", "main"], repo=self.work_dir)

        self.nightly(
            repo_updates={"branches": "main", "report": "report", "timeout": "0.1s"},
            complete=True,
        )
        result = self.run_runner("main", "timeout.log", expected_returncode=1)
        self.assertIn("timed out", result.stdout.lower())
        metadata = self.repos_dir / "testrepo" / "main.json"
        self.assertTrue(metadata.exists())
        contents = metadata.read_text()
        self.assertIn('"commit"', contents)
        self.assertIn('"time"', contents)

    def test_runner_writes_metadata_for_successful_run(self) -> None:
        self.makefile("add simple nightly target", ["echo ok"])
        self.git(["push", "origin", "main"], repo=self.work_dir)

        self.nightly(repo_updates={"branches": "main"}, complete=True)
        self.run_runner("main", "metadata-success.log")
        metadata = self.repos_dir / "testrepo" / "main.json"
        self.assertTrue(metadata.exists())
        contents = metadata.read_text()
        self.assertIn('"commit"', contents)
        self.assertIn('"time"', contents)

    def test_load_normalizes_baseurl_with_trailing_slash(self) -> None:
        self.write_config(repo_updates={}, default_updates={"baseurl": "https://nightlies.example"})
        runner = NightlyRunner(str(self.config_file))
        runner.load()
        self.assertEqual(runner.base_url, "https://nightlies.example/")

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
        old_cwd, _ = self.with_cwd(self.tmpdir)
        try:
            runner.run()
        finally:
            os.chdir(old_cwd)
        repo = runner.repos[0]
        ran = [branch.name for branch in repo.runnable]
        if complete:
            for branch in repo.runnable:
                branch.config["commit"] = branch.current_commit
                branch.save_metadata()
                print(
                    "[debug:complete-write] "
                    f"branch={branch.name} "
                    f"current_commit={branch.current_commit} "
                    f"metadata={branch.lastcommit.read_text()}"
                )
        return ran

    def format_repo_state(self, label: str) -> str:
        repo_dir = self.repos_dir / "testrepo"
        checkout = repo_dir / ".checkout"
        details: list[str] = [f"[debug:{label}]"]
        details.append(f"repo_dir_exists={repo_dir.exists()}")
        details.append(f"checkout_exists={checkout.exists()}")
        if checkout.exists():
            cp = subprocess.run(
                ["git", "-C", str(checkout), "branch", "-r"],
                capture_output=True,
                text=True,
            )
            if cp.returncode == 0:
                details.append("remote_branches=" + " | ".join(line.strip() for line in cp.stdout.splitlines()))
            else:
                details.append(f"remote_branches=<error:{cp.stderr.strip()}>")

        for branch in ("main", "hotfix/new"):
            filename = branch.replace("%", "_25").replace("/", "_2f") + ".json"
            metadata = repo_dir / filename
            if metadata.exists():
                details.append(f"{branch}.json={metadata.read_text()}")
            else:
                details.append(f"{branch}.json=<missing>")
            if checkout.exists():
                cp = subprocess.run(
                    ["git", "-C", str(checkout), "rev-parse", f"origin/{branch}"],
                    capture_output=True,
                    text=True,
                )
                if cp.returncode == 0:
                    details.append(f"origin/{branch}={cp.stdout.strip()}")
                else:
                    details.append(f"origin/{branch}=<missing:{cp.stderr.strip()}>")

        logs = sorted(self.logs_dir.glob("*.log"))
        if logs:
            latest = logs[-1]
            tail = latest.read_text(errors="replace").splitlines()[-20:]
            details.append(f"latest_log={latest.name}")
            details.append("latest_log_tail:\n" + "\n".join(tail))
        else:
            details.append("latest_log=<none>")
        return "\n".join(details)

    def dump_repo_state(self, label: str) -> None:
        print(self.format_repo_state(label))

    def run_runner(
        self,
        branch: str,
        log_name: str,
        expected_returncode: int = 0,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["python3", "runner.py", str(self.config_file), "testrepo", branch, log_name],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, expected_returncode, msg=result.stdout + "\n" + result.stderr)
        return result

    def published_report(self, result: subprocess.CompletedProcess[str]) -> Path:
        marker = "Publishing report directory "
        for line in result.stdout.splitlines():
            if marker in line and " to " in line:
                return Path(line.split(" to ", 1)[1].strip())
        self.fail("Could not find published report directory in runner output")

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

    def makefile_text(self, commands: list[str], target: str = "nightly") -> str:
        lines = [f"{target}:\n"]
        for command in commands:
            cmd = command.lstrip()
            if not cmd.startswith("@"):
                cmd = "@" + cmd
            lines.append(f"\t{cmd}\n")
        return "".join(lines)

    def makefile(
        self,
        message: str,
        commands: list[str],
        repo: Path | None = None,
        target: str = "nightly",
    ) -> None:
        self.commit(repo or self.work_dir, "Makefile", self.makefile_text(commands, target), message)

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
