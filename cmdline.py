#!/usr/bin/env python3

import sys
import os
import nightlies

def run():
    CONF_FILE = os.getenv("NIGHTLY_CONF_FILE")
    assert CONF_FILE, "ERROR: could not find $NIGHTLY_CONF_FILE environment variable"
    
    runner = nightlies.NightlyRunner(CONF_FILE)
    runner.load()
    
    _, cmd, *args = sys.argv
    
    if cmd == "emoji":
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
