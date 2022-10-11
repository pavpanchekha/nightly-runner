import sys
sys.path.insert(0, "/data/pavpan/nightlies/")
import os
os.chdir("/data/pavpan/nightlies")
import bottle
import server
server.CONF_FILE = "conf/nightlies.conf"
application = bottle.default_app()
