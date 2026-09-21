"""An error in one page must not close the app.

PyQt aborts the process when an exception escapes a slot, which is how the
Analysis page used to kill the exe with no message at all. The guard keeps the
app running, writes the traceback beside the database and tells the user in
plain words.
"""
import os
import sys

import _harness as H            # must be first

from PyQt5.QtWidgets import QApplication, QMessageBox

import database.db_manager as dbm
import services.crash_guard as cg

H.fresh_db('crash.db')
app = QApplication.instance() or QApplication([])

shown = []
QMessageBox.exec_ = lambda self: shown.append(
    (self.windowTitle(), self.text(), self.informativeText()))

cg.install()
H.check(sys.excepthook is not sys.__excepthook__, 'the guard is installed')


def boom(n):
    return 1 / n


# what a slot raising looks like: PyQt routes it to sys.excepthook
try:
    boom(0)
except ZeroDivisionError:
    sys.excepthook(*sys.exc_info())

log = cg.log_path()
H.check(os.path.isfile(log), 'the traceback is written to {}'.format(log))
text = open(log, encoding='utf-8').read()
H.check('ZeroDivisionError' in text and 'boom' in text,
        'the log names the error and where it happened')
H.check(os.path.dirname(os.path.dirname(log)) ==
        os.path.dirname(os.path.abspath(dbm.DB_PATH)),
        'the log sits beside the database, not in the program folder')
H.check(len(shown) == 1 and 'untouched' in shown[0][1],
        'the user is told, and told their data is safe: {}'.format(shown[0][1][:60]))
H.check('ZeroDivisionError' in shown[0][2], 'with the error itself: {}'.format(
    shown[0][2].splitlines()[0]))

# the same error again (a repainting page can raise every frame)
for _ in range(5):
    try:
        boom(0)
    except ZeroDivisionError:
        sys.excepthook(*sys.exc_info())
H.check(len(shown) == 1, 'a repeating error does not stack dialogs ({})'.format(len(shown)))
H.check(open(log, encoding='utf-8').read().count('=== ') == 6,
        'but every occurrence is still logged')

# a different error does get its own dialog
try:
    {}['nope']
except KeyError:
    sys.excepthook(*sys.exc_info())
H.check(len(shown) == 2, 'a different error is shown')

# the app is still alive — that is the whole point
H.check(QApplication.instance() is not None and app.topLevelWidgets() is not None,
        'the application is still running after all of that')

# Ctrl+C still stops it (the default hook prints it — keep that out of the
# test's own output)
import contextlib
import io
try:
    raise KeyboardInterrupt()
except KeyboardInterrupt:
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            sys.excepthook(*sys.exc_info())
        except SystemExit:
            pass
H.check(len(shown) == 2, 'Ctrl+C is not turned into a dialog')

H.finish()
