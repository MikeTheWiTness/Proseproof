import os, sys


def app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def app_path(rel):
    return os.path.join(app_dir(), rel)
