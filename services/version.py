"""services/version.py — the application version, in one place.

Three things read this constant and nothing else: the main window title (so the
user can say which version they are running), `tools/build_release.py` (which
writes it into `installer/version.iss` for Inno Setup, because an .iss file
cannot import Python), and the installer's own file name. Bump it here and the
whole release follows.

Keep it a plain dotted string — Inno Setup's VersionInfoVersion parses it.
"""

APP_VERSION = '1.1.0'
