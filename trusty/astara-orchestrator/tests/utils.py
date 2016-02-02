
import datetime
import os
import sys


def log(msg):
    print '%s %s: %s' % (
        datetime.datetime.utcnow(), os.path.basename(sys.argv[0]), msg)
