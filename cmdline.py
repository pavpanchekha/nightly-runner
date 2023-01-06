#!/usr/bin/env python3

import sys
import os
import nightlies
from pathlib import Path
import argparse
import shutil
import time

def info(runner : nightlies.NightlyRunner, args : argparse.Namespace) -> None:
    print(f"dir={runner.dir}")
    print(f"config_file={runner.config_file}")
    print(f"log_dir={runner.log_dir}")
    print(f"pid_file={runner.pid_file}")
    print(f"info_file={runner.info_file}")

def emoji(runner : nightlies.NightlyRunner, args : argparse.Namespace) -> None:
    runner.add_info("emoji", args.emoji)

def url(runner : nightlies.NightlyRunner, args : argparse.Namespace) -> None:
    runner.add_info("url", args.url)

def img(runner : nightlies.NightlyRunner, args : argparse.Namespace) -> None:
    runner.add_info("img", args.url)

def publish(runner : nightlies.NightlyRunner, args : argparse.Namespace) -> None:
    assert runner.report_dir.exists(), f"Report dir {runner.report_dir} does not exist"
    repo = runner.data["repo"]
    name = args.name if args.name else str(int(time.time()))

    runner.log(4, f"Publishing {args.path} to {dest_dir}")
    dest_dir = runner.report_dir / repo / name
    shutil.copytree(args.path, dest_dir)
    if runner.report_group:
        runner.log(4, f"Changing group owner of {args.path} to {runner.report_group}")
        shutil.chown(args.path, group=runner.report_group)
        for dpath, dirs, files in os.walk(str(args.path.resolve())):
            for dname in dirs:
                shutil.chown(os.path.join(dirpath, dname), group=runner.report_group)
            for fname in files:
                shutil.chown(os.path.join(dirpath, fname), group=runner.report_group)

    url_base = os.path.join(self.base_url, repo, name)
    runner.add_info("url", url_base)
    if args.image:
        assert args.image.is_relative_to(args.path), \
            "Image path {args.image} is not within the pusblished path {args.path}"
        relpath = args.image.relative_to(args.path)
        runner.add_info("img", os.path.join(url_base, str(relpath)))
    
def download(runner : nightlies.NightlyRunner, args : argparse.Namespace) -> None:
    assert runner.report_dir.exists(), f"Report dir {runner.report_dir} does not exist"
    repo = runner.data["repo"]
    src = runner.report_dir / repo / args.name
    dst = Path.cwd() / (args.to or args.name)
    runner.log(4, f"Copying {src} to {dst}")
    shutil.copytree(src, dst)
    
# Command handling code

def load():
    CONF_FILE = os.getenv("NIGHTLY_CONF_FILE")
    assert CONF_FILE, "ERROR: could not find $NIGHTLY_CONF_FILE environment variable"
    
    runner = nightlies.NightlyRunner(CONF_FILE)
    old_cwd = Path.cwd()
    os.chdir(runner.self_dir)
    runner.load()
    os.chdir(old_cwd)
    
    return runner
    
def valid_url(s : str) -> None:
    if "://" in s:
        return s
    else:
        raise ValueError("ERROR: <url> must have format http[s]://...")
    
def valid_path(s : str) -> None:
    p = Path(s).resolve()
    if not p.exists():
        raise ValueError(f"ERROR: {s!r} does not exist")
    return p

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="nightly-results")
    subparser = parser.add_subparsers()

    p = subparser.add_parser("info", help="Show path information for the nightlies")
    p.set_defaults(func=info)

    p = subparser.add_parser("emoji", help="Show an emoji in Slack")
    p.add_argument("emoji")
    p.set_defaults(func=emoji)

    p = subparser.add_parser("url", help="Output to link to in Slack")
    p.add_argument("url", type=valid_url)
    p.set_defaults(func=url)

    p = subparser.add_parser("img", help="Image to show in Slack")
    p.add_argument("url", type=valid_url)
    p.set_defaults(func=img)

    p = subparser.add_parser("publish", help="Publish a folder")
    p.add_argument("path", type=valid_path)
    p.add_argument("--image", action="store", type=valid_path, help="Indicates a published image to show in Slack")
    p.add_argument("--name", action="store", default=None, help="Overrides the default (timestamp) name")
    p.set_defaults(func=publish)

    p = subparser.add_parser("download", help="Download a previously-published file or folder")
    p.add_argument("name")
    p.add_argument("to", default=None)
    p.set_defaults(func=download)

    args = parser.parse_args()
    runner = load()
    args.func(runner, args)
