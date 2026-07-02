import threading

_log_func = None
_log_lock = threading.Lock()


def set_log_func(func):
    global _log_func
    _log_func = func


def log(msg):
    if _log_func:
        with _log_lock:
            _log_func(msg)
