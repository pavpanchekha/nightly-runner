import sys
sys.path.insert(0, "/data/pavpan/nightlies/")
import os
os.chdir("/data/pavpan/nightlies")
import bottle
import server
application = bottle.default_app()
