from typing import List, TYPE_CHECKING
import re
import slack

if TYPE_CHECKING:
    import nightlies

APT_LINE_RE = re.compile(r"^(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded\.$", re.MULTILINE)

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

def post(res : slack.Response) -> None:
    res.add(slack.TextBlock("`apt`: Reran all branches because a package updated"))

