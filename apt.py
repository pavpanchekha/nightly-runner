from dataclasses import dataclass
from typing import List, Sequence, TYPE_CHECKING
import re
import subprocess

if TYPE_CHECKING:
    import nightlies

APT_LINE_RE = re.compile(r"^(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded\.$", re.MULTILINE)
APT_INST_RE = re.compile(r"^Inst (\S+)(?: \[([^\]]+)\])? \(([^)]+)\)")
APT_REMV_RE = re.compile(r"^Remv (\S+) \[([^\]]+)\]")

NEW_PACKAGE_VERSION = "<none>"
REMOVED_PACKAGE_VERSION = "<removed>"


@dataclass(frozen=True)
class AptPackageUpdate:
    package: str
    before: str
    after: str

def _format_cmd(cmd: Sequence[object]) -> str:
    return " ".join(str(part) for part in cmd)


def add_repositories(runner: "nightlies.NightlyRunner", repos: Sequence[str]) -> List[str]:
    failed: List[str] = []
    for repo in repos:
        runner.log(1, f"Adding apt repository {repo}")
        if runner.dryrun:
            continue
        try:
            runner.exec(2, ["sudo", "add-apt-repository", "--yes", repo])
        except subprocess.CalledProcessError as e:
            failed.append(repo)
            runner.log(
                1,
                f"Failed to add apt repository {repo}: process {_format_cmd(e.cmd)} returned error code {e.returncode}",
            )
        except OSError as e:
            failed.append(repo)
            runner.log(1, f"Failed to add apt repository {repo}: {e}")
    return failed


def _parse_updates(stdout: str) -> List[AptPackageUpdate]:
    updates: List[AptPackageUpdate] = []
    for line in stdout.splitlines():
        inst = APT_INST_RE.match(line)
        if inst:
            package, before, after_info = inst.group(1, 2, 3)
            after = after_info.split(" ", 1)[0]
            updates.append(AptPackageUpdate(package, before or NEW_PACKAGE_VERSION, after))
            continue

        remv = APT_REMV_RE.match(line)
        if remv:
            package, before = remv.group(1, 2)
            updates.append(AptPackageUpdate(package, before, REMOVED_PACKAGE_VERSION))

    return updates


def check_updates(runner : "nightlies.NightlyRunner", pkgs : List[str]) -> List[AptPackageUpdate]:
    runner.log(1, f"Checking for updates to apt packages {' '.join(pkgs)}")
    stdout = runner.exec(2, ["sudo", "apt", "install", "--dry-run"] + pkgs).stdout.decode("latin1")

    # Parse the `apt` output, ugh
    match = APT_LINE_RE.search(stdout)
    if not match:
        raise IOError("apt: Could not find package line in `apt` results")

    num_u, num_i, num_r, _ = match.group(1, 2, 3, 4)
    if not (int(num_u) or int(num_i) or int(num_r)):
        return []

    updates = _parse_updates(stdout)
    if not updates:
        raise IOError("apt: Could not parse package updates from `apt` results")
    return updates

def install(runner : "nightlies.NightlyRunner", pkgs : List[str]) -> None:
    runner.log(1, f"Installing apt packages {' '.join(pkgs)}")
    runner.exec(2, ["sudo", "apt", "install", "--yes"] + pkgs)
