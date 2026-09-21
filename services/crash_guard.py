"""services/crash_guard.py — an error in the app must not close the app.

PyQt calls `qFatal` when a Python exception escapes a slot, so until now one
unhandled error anywhere in the UI killed the exe with no message: the user
saw the window vanish (the Analysis page did exactly that for months, from a
stale duplicate function). An engineer who was half-way through a record lost
it and had no idea why.

So: install a hook that keeps the app alive, writes the traceback to a log
file beside the database, and shows the user what happened in plain words.
The log is ours — it holds file paths and SQL, never a password or a token,
because nothing here prints the sync config.
"""
import datetime
import os
import sys
import traceback

_LOG = None
_SEEN = set()          # the same error at the same place is logged once a run


def log_path() -> str:
    """Beside the database, so the log ships with a backup of the data and a
    rebuilt exe does not lose it."""
    global _LOG
    if _LOG is None:
        from database.db_manager import DB_PATH
        d = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), 'logs')
        os.makedirs(d, exist_ok=True)
        _LOG = os.path.join(d, 'errors.log')
    return _LOG


def write(kind: str, text: str) -> str:
    path = log_path()
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        # keep the file small enough to send by e-mail
        if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 * 1024:
            os.replace(path, path + '.1')
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(f"\n=== {stamp} · {kind} ===\n{text}\n")
    except OSError:
        pass                                  # a log that cannot be written is not fatal
    return path


def install(app=None):
    """Catch what escapes a slot. KeyboardInterrupt still stops the app."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc, tb)
            return
        text = ''.join(traceback.format_exception(exc_type, exc, tb))
        path = write('unhandled', text)
        where = ''.join(traceback.format_tb(tb)[-1:]).strip()
        key = f"{exc_type.__name__}:{where}"
        if key in _SEEN:                      # a repeating error must not stack dialogs
            return
        _SEEN.add(key)
        try:
            from PyQt5.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is None:
                return
            box = QMessageBox()
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle('Something went wrong')
            box.setText('The app hit an error and stopped what it was doing.\n'
                        'Your data is untouched — the page can be opened again.')
            box.setInformativeText(f"{exc_type.__name__}: {exc}\n\nWritten to:\n{path}")
            box.setDetailedText(text)
            box.exec_()
        except Exception:                     # noqa: BLE001 — never fail inside the hook
            pass

    sys.excepthook = hook
    return hook
