from typing import List, Sequence, TYPE_CHECKING
import re
import subprocess

if TYPE_CHECKING:
    import nightlies

APT_LINE_RE = re.compile(r"^(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded\.$", re.MULTILINE)

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


def check_updates(runner : "nightlies.NightlyRunner", pkgs : List[str]) -> bool:
    runner.log(1, f"Checking for updates to apt packages {' '.join(pkgs)}")
    res = runner.exec(2, ["sudo", "apt", "install", "--dry-run"] + pkgs)

    # Parse the `apt` output, ugh
    match = APT_LINE_RE.search(res.stdout.decode("latin1"))
    if not match:
        raise IOError("apt: Could not find package line in `apt` results")

    num_u, num_i, num_r, _ = match.group(1, 2, 3, 4)
    return bool(int(num_u) or int(num_i) or int(num_r))

def install(runner : "nightlies.NightlyRunner", pkgs : List[str]) -> None:
    runner.log(1, f"Installing apt packages {' '.join(pkgs)}")
    runner.exec(2, ["sudo", "apt", "install", "--yes"] + pkgs)

