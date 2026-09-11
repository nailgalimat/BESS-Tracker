"""Run the test suite.

    python tests\\run.py                 fast tests
    python tests\\run.py --real          also test_real_*: generates real reports
                                         from the SCADA exports (several minutes)
    python tests\\run.py planner cycles  only tests whose name contains a word

Each test runs in its own process and prints a final RESULT line; that line,
not the exit code, decides the outcome (see _harness.py). Run --real before
every exe build and before a report goes to a customer.
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FAST_TIMEOUT, REAL_TIMEOUT = 600, 3600


def main():
    real = '--real' in sys.argv
    words = [a for a in sys.argv[1:] if not a.startswith('--')]
    files = sorted(glob.glob(os.path.join(HERE, 'test_*.py')))
    if not real:
        files = [f for f in files if not os.path.basename(f).startswith('test_real_')]
    if words:
        files = [f for f in files if any(w in os.path.basename(f) for w in words)]
    if not files:
        print('no tests selected')
        return 1

    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    results = []
    for f in files:
        name = os.path.basename(f)[:-3]
        timeout = REAL_TIMEOUT if name.startswith('test_real_') else FAST_TIMEOUT
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, f], capture_output=True, text=True,
                               encoding='utf-8', errors='replace', env=env,
                               timeout=timeout)
            out = (p.stdout or '') + (p.stderr or '')
        except subprocess.TimeoutExpired as e:
            out = ((e.stdout or '') if isinstance(e.stdout, str) else '') + \
                  '\nTIMEOUT after {} s'.format(timeout)
        status = ('PASS' if 'RESULT PASS' in out else
                  'SKIP' if 'RESULT SKIP' in out else 'FAIL')
        dt = time.time() - t0
        results.append((name, status))
        print('{:<34} {}  ({:.0f} s)'.format(name, status, dt), flush=True)
        if status == 'FAIL':
            tail = [ln for ln in out.splitlines() if ln.strip()][-14:]
            for ln in tail:
                print('      ' + ln)
        elif status == 'SKIP':
            print('      ' + next(ln for ln in out.splitlines() if 'RESULT SKIP' in ln))

    n_fail = sum(1 for _, s in results if s == 'FAIL')
    n_pass = sum(1 for _, s in results if s == 'PASS')
    n_skip = sum(1 for _, s in results if s == 'SKIP')
    print('\n{} passed, {} failed, {} skipped'.format(n_pass, n_fail, n_skip))
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main())
