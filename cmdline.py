#!/usr/bin/env python3

import sys
import os
import nightlies
from pathlib import Path

def run():
    CONF_FILE = os.getenv("NIGHTLY_CONF_FILE")
    assert CONF_FILE, "ERROR: could not find $NIGHTLY_CONF_FILE environment variable"
    
    nightly_path = Path(__file__).resolve().parent
    old_cwd = Path.cwd()
    os.chdir(nightly_path)
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    os.chdir(old_cwd)
    
    _, cmd, *args = sys.argv
    
    if cmd == "info":
        assert len(args) == 0, "usage: nightly-resutls info"
        print(f"dir={runner.dir}")
        print(f"config_file={runner.config_file}")
        print(f"log_dir={runner.log_dir}")
        print(f"pid_file={runner.pid_file}")
        print(f"info_file={runner.info_file}")
        return
    elif cmd == "emoji":
        assert len(args) == 1, "usage: nightly-results emoji <emoji name>"
    elif cmd == "url":
        assert len(args) == 1, "usage: nightly-results url <url>"
        assert "://" in args[0], "ERROR: <url> must have format http[s]://..."
    elif cmd == "img":
        assert len(args) == 1, "usage: nightly-results img <url>"
        assert "://" in args[0], "ERROR: <url> must have format http[s]://..."
    else:
        assert False, f"{cmd} is not a known nightly-results command"
    
    runner.add_info(cmd, *args)

if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(e)
        sys.exit(1)
