#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import configparser
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

# Ensure direct execution (uv run test/test_nightlies.py) resolves project modules from repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nightlies import NightlyRunner
import apt
import server


class FakeRunner:
    def __init__(self, dryrun: bool = False) -> None:
        self.dryrun = dryrun
        self.logs: list[tuple[int, str]] = []
        self.commands: list[list[str]] = []

    def log(self, level: int, message: str) -> None:
        self.logs.append((level, message))

    def exec(self, level: int, cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")


class TestApt(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="apt-test-"))
        self.source_list = self.tmpdir / "sources.list"
        self.sources_dir = self.tmpdir / "sources.list.d"
        self.sources_dir.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def test_add_repositories_skips_existing_ppa(self) -> None:
        (self.sources_dir / "owner-name.list").write_text(
            "deb https://ppa.launchpadcontent.net/owner/name/ubuntu noble main\n"
        )
        runner = FakeRunner()

        with mock.patch.object(apt, "_has_repository", return_value=True):
            failed = apt.add_repositories(runner, ["ppa:owner/name"])

        self.assertEqual(failed, [])
        self.assertEqual(runner.commands, [])
        self.assertIn((1, "Apt repository ppa:owner/name already present; skipping"), runner.logs)

    def test_add_repositories_adds_missing_ppa(self) -> None:
        runner = FakeRunner()

        with mock.patch.object(apt, "_has_repository", return_value=False):
            failed = apt.add_repositories(runner, ["ppa:owner/name"])

        self.assertEqual(failed, [])
        self.assertEqual(
            runner.commands,
            [["sudo", "add-apt-repository", "--yes", "ppa:owner/name"]],
        )

    def test_has_repository_matches_launchpad_sources(self) -> None:
        (self.source_list).write_text(
            "Types: deb\n"
            "URIs: https://ppa.launchpadcontent.net/owner/name/ubuntu\n"
            "Suites: noble\n"
            "Components: main\n"
        )

        self.assertTrue(apt._has_repository("ppa:owner/name", self.source_list, self.sources_dir))
        self.assertFalse(apt._has_repository("ppa:other/name", self.source_list, self.sources_dir))


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

        self.git(["init", "--bare", "--initial-branch=main", str(self.remote_dir)], repo=self.tmpdir)
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
        self.assertEqual(first, ["main"])

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
        info = json.loads((report_dir / "nightly_info.json").read_text())
        self.assertEqual(info["repo"], "testrepo")
        self.assertEqual(info["branch"], "main")
        self.assertEqual(info["branch_filename"], "main")
        self.assertEqual(info["status"], "success")
        self.assertRegex(info["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(info["commit_short"], info["commit"][:8])
        self.assertEqual(info["log"], "publish-report.log")
        self.assertEqual(
            info["report_url"],
            "https://nightlies.example/reports/testrepo/" + report_dir.name,
        )
        self.assertEqual(
            info["log_url"],
            "https://nightlies.example/logs/publish-report.log",
        )
        self.assertIsNone(info["image_url"])
        self.assertEqual(
            info["files"],
            [
                {"path": "index.html", "gzip": False},
                {"path": "nightly_info.json", "gzip": False},
            ],
        )
        # Published reports should be moved out of the worktree so later runs start clean.
        self.assertFalse((self.repos_dir / "testrepo" / "main" / "report").exists())

    def test_runner_records_gzipped_files_in_nightly_info(self) -> None:
        self.makefile(
            "add gzipped report-producing nightly target",
            [
                "printf '<h1>ok</h1>\\n' > report/index.html",
                "printf '{\"ok\":true}\\n' > report/results.json",
            ],
        )
        self.git(["push", "origin", "main"], repo=self.work_dir)

        self.nightly(
            repo_updates={"branches": "main", "report": "report", "gzip": "*.json"},
            complete=True,
        )
        result = self.run_runner("main", "publish-gzip-report.log")
        report_dir = self.published_report(result)
        info = json.loads((report_dir / "nightly_info.json").read_text())

        self.assertEqual(
            info["files"],
            [
                {"path": "index.html", "gzip": False},
                {"path": "nightly_info.json", "gzip": False},
                {"path": "results.json.gz", "gzip": True},
            ],
        )

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
        report_dir = self.published_report(result)
        self.assertFalse((report_dir / "nightly_info.json").exists())

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
        return ran

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


class TestReportExec(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="nr-server-"))
        self.reports_dir = self.tmpdir / "reports"
        self.logs_dir = self.tmpdir / "logs"
        self.repos_dir = self.tmpdir / "repos"
        self.config_file = self.tmpdir / "nightlies.conf"
        self.reports_dir.mkdir()
        self.logs_dir.mkdir()
        self.repos_dir.mkdir()

        self.report = "testrepo/1776156991:main:dee549aa"
        self.report_dir = self.reports_dir / self.report
        self.report_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir)

    def write_executable(self, relpath: str, script: str):
        executable = self.report_dir / relpath
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text(script)
        executable.chmod(0o755)

    def test_resolve_report_exec_rejects_path_traversal(self) -> None:
        with self.assertRaises(server.bottle.HTTPError) as exc:
            server.resolve_report_exec(self.reports_dir, self.report, "../outside")
        self.assertEqual(exc.exception.status_code, 400)

    def test_resolve_report_exec_rejects_missing_file(self) -> None:
        with self.assertRaises(server.bottle.HTTPError) as exc:
            server.resolve_report_exec(self.reports_dir, self.report, "missing-tool")
        self.assertEqual(exc.exception.status_code, 404)
        self.assertIn("Missing executable", exc.exception.body)

    def test_run_report_exec_returns_stdout(self) -> None:
        self.write_executable(
            "bin/report-tool",
            "#!/bin/sh\n"
            "printf 'cwd=%s\\n' \"$PWD\"\n"
            "printf 'args=%s,%s\\n' \"$1\" \"$2\"\n",
        )
        report, executable = server.resolve_report_exec(self.reports_dir, self.report, "bin/report-tool")
        output = server.run_report_exec(report, executable, ["left", "right"])
        self.assertEqual(
            output,
            f"cwd={self.report_dir.resolve()}\nargs=left,right\n",
        )

    def test_run_report_exec_returns_500_on_nonzero_exit(self) -> None:
        self.write_executable(
            "bin/report-tool-fail",
            "#!/bin/sh\n"
            "echo 'boom' >&2\n"
            "exit 2\n",
        )
        report, executable = server.resolve_report_exec(self.reports_dir, self.report, "bin/report-tool-fail")
        with self.assertRaises(server.bottle.HTTPError) as exc:
            server.run_report_exec(self.report_dir.resolve(), executable, ["left", "right"])
        self.assertEqual(exc.exception.status_code, 500)
        self.assertIn("status 2", exc.exception.body)
        self.assertIn("boom", exc.exception.body)


if __name__ == "__main__":
    unittest.main()
