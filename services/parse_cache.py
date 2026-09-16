"""services/parse_cache.py — the month's SCADA workbooks, read once per file content

Reading the xlsx exports is most of a report run: August 2026 took 94 s, about
60 of them inside pd.read_excel. A re-generation after one edited exclusion
window reads exactly the same bytes again. This keeps what pd.read_excel
returned, keyed on

  * the SHA-256 of the file's bytes,
  * the read arguments (sheet_name, ...),
  * the pandas and openpyxl versions,

which are all that decide that result. So a replaced file is read afresh (other
hash), a library upgrade is read afresh (other key), and the loaders' own code
is not cached at all: it runs on every generation over the cached frame, so a
fix in a loader needs no invalidation.

Only files inside the month data set (report_data/ next to the database, see
month_dataset_service) are cached. Any other path — the Block Performance
page's pickers, the tests' SCADA folder — goes to pd.read_excel exactly as
before. A cache entry that cannot be read is deleted and the file read again.
"""

import hashlib
import json
import os

import pandas as pd

import database.db_manager as dbm

CACHE_FORMAT = 1
STATS = {'hits': 0, 'misses': 0, 'bypass': 0}
_sha_memo = {}


def data_root() -> str:
    """report_data/ beside the database — the exe's folder for the app, the
    test's work folder under the harness (DB_PATH is read at call time)."""
    return os.path.join(os.path.dirname(os.path.abspath(dbm.DB_PATH)), 'report_data')


def cache_dir() -> str:
    return os.path.join(data_root(), '_cache')


def file_sha256(path: str) -> str:
    """SHA-256 of a file, remembered per (path, size, mtime) within the process."""
    st = os.stat(path)
    key = (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)
    if key not in _sha_memo:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        _sha_memo[key] = h.hexdigest()
    return _sha_memo[key]


def in_data_set(path) -> bool:
    try:
        root = os.path.normcase(os.path.abspath(data_root())) + os.sep
        return os.path.normcase(os.path.abspath(str(path))).startswith(root)
    except (TypeError, ValueError):
        return False


def _key(path, kw) -> str:
    import openpyxl
    kw = dict(kw)
    kw.setdefault('sheet_name', 0)
    blob = json.dumps({'sha': file_sha256(path), 'kw': kw, 'pandas': pd.__version__,
                       'openpyxl': openpyxl.__version__, 'fmt': CACHE_FORMAT},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def read_excel(path, **kw):
    """pd.read_excel(path, **kw), from the cache when the file belongs to a
    month data set. Every call returns a fresh object (loaders modify frames)."""
    if not (isinstance(path, (str, os.PathLike)) and in_data_set(path)
            and os.path.isfile(path)):
        STATS['bypass'] += 1
        return pd.read_excel(path, **kw)
    k = _key(path, kw)
    p = os.path.join(cache_dir(), k[:2], k + '.pkl')
    if os.path.exists(p):
        try:
            out = pd.read_pickle(p)
            STATS['hits'] += 1
            return out
        except Exception:                                  # noqa: BLE001
            try:
                os.remove(p)
            except OSError:
                pass
    STATS['misses'] += 1
    out = pd.read_excel(path, **kw)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = '{}.{}.tmp'.format(p, os.getpid())
        pd.to_pickle(out, tmp)
        os.replace(tmp, p)
    except Exception as e:                                 # noqa: BLE001
        print('[parse_cache] not cached ({}): {}'.format(os.path.basename(str(path)), e))
    return out
