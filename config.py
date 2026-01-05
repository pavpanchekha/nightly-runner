#!/usr/bin/env python3

from typing import Dict, Optional
from pathlib import Path
import configparser

class Config:
    def __init__(self, config_file: str):
        self.config_file = Path(config_file).resolve()
        assert self.config_file.is_file(), f"Configuration file {self.config_file} is not a file"
        
        self.config = configparser.ConfigParser()
        self.config.read(str(self.config_file))
        
        defaults = self.config.defaults()
        self.base_url = defaults.get("baseurl")
        if self.base_url and not self.base_url.endswith("/"):
            self.base_url += "/"
        self.repos_dir = Path(defaults.get("repos", ".")).resolve()
        self.reports_dir = Path(defaults.get("reports", "reports")).resolve()
        self.logs_dir = Path(defaults.get("logs", "logs")).resolve()
        
        self.secrets = configparser.ConfigParser()
        if defaults.get("secrets"):
            for file in Path(defaults["secrets"]).iterdir():
                if not file.name.endswith(".conf"):
                    continue
                with file.open() as f:
                    self.secrets.read_file(f, source=f.name)

    def get_repo_config(self, repo_name: str) -> Dict[str, str]:
        return dict(self.config[repo_name])

    def get_slack_token(self, repo_name: str) -> Optional[str]:
        repo_config = self.config[repo_name]
        slack_channel = repo_config.get("slack")
        if not slack_channel or slack_channel not in self.secrets:
            return None
        return self.secrets[slack_channel]["slack"]


def escape_branch_filename(branch: str) -> str:
    return branch.replace("%", "_25").replace("/", "_2f")


def parse_size(size: str | None) -> int | None:
    if size is None:
        return size
    units = {"kb": 1024, "k": 1024, "mb": 1024**2, "m": 1024**2, "gb": 1024**3, "g": 1024**3}
    size = size.lower()
    for unit, multiplier in units.items():
        if size.endswith(unit):
            return int(float(size[:-len(unit)]) * multiplier)
    return int(size)


def format_size_slurm(size_bytes: int) -> str:
    if size_bytes % (1024**3) == 0:
        return f"{size_bytes // (1024**3)}G"
    elif size_bytes % (1024**2) == 0:
        return f"{size_bytes // (1024**2)}M"
    elif size_bytes % 1024 == 0:
        return f"{size_bytes // 1024}K"
    else:
        return str(size_bytes)


def parse_cores(cores: str | None) -> int | None:
    if cores is None or cores.lower() == "all":
        return None
    return int(cores)


class BranchConfig:
    def __init__(self, config: Config, repo_name: str, branch_name: str):
        self.config = config
        self.repo_name = repo_name.split("/")[-1]
        self.branch_name = branch_name
        self.branch_filename = escape_branch_filename(branch_name)
        
        repo_config = config.get_repo_config(repo_name)
        
        self.repo_dir = config.repos_dir / self.repo_name
        self.branch_dir = self.repo_dir / self.branch_filename
        self.metadata_file = self.repo_dir / (self.branch_filename + ".json")
        
        report_dir_name = repo_config.get("report")
        self.report_dir = self.branch_dir / report_dir_name if report_dir_name else None
        
        image_file_name = repo_config.get("image")
        self.image_file = self.report_dir / image_file_name if self.report_dir and image_file_name else None
        
        self.timeout = repo_config.get("timeout")
        self.gzip = repo_config.get("gzip", "")
        self.warn_size = repo_config.get("warn_size", "1gb")
        
        self.base_url = config.base_url
        self.reports_dir = config.reports_dir
        self.logs_dir = config.logs_dir
        self.slack_token = config.get_slack_token(repo_name)
