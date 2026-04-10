import sys


def is_win11():
    return sys.platform == 'win32' and sys.getwindowsversion().build >= 22000
