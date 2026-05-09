#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

# Ensure direct execution resolves project modules from repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import apt


class FakeRunner:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.commands: list[list[str]] = []
        self.logs: list[str] = []

    def log(self, level: int, message: str) -> None:
        self.logs.append(f"{level}:{message}")

    def exec(self, level: int, cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout.encode("latin1"))


class TestAptParsing(unittest.TestCase):
    def test_check_updates_returns_empty_list_when_no_changes(self) -> None:
        stdout = """\
0 upgraded, 0 newly installed, 0 to remove and 3 not upgraded.
"""
        runner = FakeRunner(stdout)
        updates = apt.check_updates(runner, ["cargo"])
        self.assertEqual(updates, [])

    def test_check_updates_parses_upgraded_package(self) -> None:
        stdout = """\
1 upgraded, 0 newly installed, 0 to remove and 3 not upgraded.
Inst cargo [1.80.0-1] (1.81.0-1 Ubuntu:24.04/noble-updates [amd64])
Conf cargo (1.81.0-1 Ubuntu:24.04/noble-updates [amd64])
"""
        runner = FakeRunner(stdout)
        updates = apt.check_updates(runner, ["cargo"])
        self.assertEqual(
            updates,
            [apt.AptPackageUpdate(package="cargo", before="1.80.0-1", after="1.81.0-1")],
        )

    def test_check_updates_parses_install_and_removal(self) -> None:
        stdout = """\
0 upgraded, 1 newly installed, 1 to remove and 3 not upgraded.
Inst foo (2.0.0 Ubuntu:24.04/noble [amd64])
Remv bar [1.4.9-1]
"""
        runner = FakeRunner(stdout)
        updates = apt.check_updates(runner, ["foo"])
        self.assertEqual(
            updates,
            [
                apt.AptPackageUpdate(
                    package="foo",
                    before=apt.NEW_PACKAGE_VERSION,
                    after="2.0.0",
                ),
                apt.AptPackageUpdate(
                    package="bar",
                    before="1.4.9-1",
                    after=apt.REMOVED_PACKAGE_VERSION,
                ),
            ],
        )

    def test_check_updates_fails_when_changes_have_no_parseable_lines(self) -> None:
        stdout = """\
1 upgraded, 0 newly installed, 0 to remove and 3 not upgraded.
"""
        runner = FakeRunner(stdout)
        with self.assertRaisesRegex(IOError, "Could not parse package updates"):
            apt.check_updates(runner, ["cargo"])


if __name__ == "__main__":
    unittest.main()
