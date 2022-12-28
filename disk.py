#!/usr/bin/env python3

from pathlib import Path
import nightlies
import os
import shutil

class DiskUsage:
    def __init__(self, path, repo=None):
        self.path = path.absolute()

        self.used = 0
        self.available = float("inf")
        self.by_extension = {}

        if repo is not None:
            self.repo = repo
            self.by_repo = None
        else:
            self.repo = None
            self.by_repo = {}

    def scan(self) -> None:
        path = str(self.path.absolute())
        for dirpath, dnames, files in os.walk(path):
            with os.scandir(dirpath) as s:
                for entry in s:
                    stat = s.stat(follow_links=False)
                    self.available += stat.st_size
                    self.by_extension[Path(entry.path).suffix] += stat.st_size
        self.available = shutil.disk_usage(path).free

    def add(self, du) -> None:
        try:
            relative = du.absolute().path.relative_to(self.path)
        except ValueError:
            raise ValueError(f"{du.path} is not a subpath of {self.path}")

        # Either a per-repo option or an aggregate option
        assert du.repo or du.by_repo is not None
        assert not du.repo or du.by_repo is None

        self.used += du.used
        self.available = min(du.available, self.available)
        for k, v in du.by_extension.items():
            self.by_extension[k] = du.by_extension[k] + self.by_extension[k].get(0)
        for k, v in du.by_repo.items():
            self.by_repo[k] = du.by_repo[k] + self.by_repo[k].get(0)

    def to_json(self):
        return {
            "path": str(self.path),
            "repo": self.repo,
            "used": self.used,
            "available": self.available,
            "extensions": self.by_extension,
            "repos": self.by_repo,
        }

    @classmethod
    def from_json(self, json):
        v = cls(Path(json["path"]), json["repo"])
        v.used = json["used"]
        v.available = json["available"]
        v.by_extension = json["extensions"]
        v.by_repo = json["repos"]

# From https://stackoverflow.com/questions/1094841/get-human-readable-version-of-file-size
def format_size(num : float, suffix:str="B") -> str:
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"

def usage_stats(runner):
    log_usage = DiskUsage(runner.log_dir)
    log_usage.scan()

    repo_usage = DiskUsage(Path("."))
    for repo in runner.repos:
        du = DiskUsage(repo.dir, repo.name)
        du.scan()
        repo_usage.add(du)

    return {
        "logs": log_usage.to_json(),
        "repos": repo_usage.to_json(),
    }
