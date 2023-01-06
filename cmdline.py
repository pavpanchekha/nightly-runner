#!/usr/bin/env python3

import sys
import os
import nightlies
from pathlib import Path
import argparse

def load():
    CONF_FILE = os.getenv("NIGHTLY_CONF_FILE")
    assert CONF_FILE, "ERROR: could not find $NIGHTLY_CONF_FILE environment variable"
    
    nightly_path = Path(__file__).resolve().parent
    old_cwd = Path.cwd()
    os.chdir(nightly_path)
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    os.chdir(old_cwd)
    
    return runner

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
    
def valid_url(s : str) -> None:
    if "://" in s:
        return
    else:
        raise ValueError("ERROR: <url> must have format http[s]://...")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="nightly-results")
    subparser = parser.add_subparsers()

    info_p = subparser.add_parser("info", help="Show path information for the nightlies")
    info_p.set_defaults(func=info)

    emoji_p = subparser.add_parser("emoji", help="Show an emoji in Slack")
    emoji_p.add_argument("emoji")
    emoji_p.set_defaults(func=emoji)

    url_p = subparser.add_parser("url", help="Output to link to in Slack")
    url_p.add_argument("url", type=valid_url)
    url_p.set_defaults(func=url)

    img_p = subparser.add_parser("img", help="Image to show in Slack")
    img_p.add_argument("url", type=valid_url)
    img_p.set_defaults(func=img)

    args = parser.parse_args()
    runner = load()
    args.func(runner, args)