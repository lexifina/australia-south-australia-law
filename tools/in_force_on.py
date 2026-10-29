"""What a South Australian Act, or one of its provisions, said on a day.

    python tools/in_force_on.py 2015-06-01 constitution-act-1934 s-028
    python tools/in_force_on.py 2015-06-01 constitution-act-1934          (the whole Act)
    python tools/in_force_on.py 2015-06-01 constitution-act-1934 --commit (the commit that holds that day's text)

It reads the In-force trailer of every commit on main, takes the last commit in force on or before the day (commits
are in the order they took effect), and prints the files from that commit. A provision may be given as a file name
(s-028, sch-1/cl-003) or as "s 28".
"""
import re
import subprocess
import sys


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, check=True).stdout.decode('utf-8')


def commit_on(day: str, ref: str = 'main') -> str | None:
    out = git('log', '--first-parent', '--reverse', '--format=%H%x00%(trailers:key=In-force,valueonly,separator=,)', ref)
    best = None
    for line in out.splitlines():
        if '\x00' not in line:
            continue
        h, d = line.split('\x00', 1)
        d = d.strip()
        if d and d <= day:
            best = h
        elif d and d > day:
            break
    return best


def key_of(p: str) -> str:
    p = p.strip()
    m = re.match(r'^s\s+(\d+)([A-Za-z]*)$', p)
    if m:
        return f's-{int(m.group(1)):03d}{m.group(2)}'
    return p[:-3] if p.endswith('.md') else p


def main():
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')   # the law text is UTF-8, whatever the console uses
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    if len(a) < 2:
        raise SystemExit(__doc__)
    day, act = a[0], a[1].strip('/')
    c = commit_on(day)
    if c is None:
        raise SystemExit(f'nothing in force on {day}')
    if '--commit' in sys.argv:
        print(git('show', '-s', '--format=%H%n%an%n%n%B', c))
        return
    if len(a) >= 3:
        path = f'{act}/{key_of(a[2])}.md'
        r = subprocess.run(['git', 'show', f'{c}:{path}'], capture_output=True)
        if r.returncode:
            raise SystemExit(f'{path}: not in force on {day}')
        sys.stdout.write(r.stdout.decode('utf-8'))
        return
    files = git('ls-tree', '-r', '--name-only', c, '--', act + '/').split()
    if not files:
        raise SystemExit(f'{act}: not in force on {day}')
    for f in files:
        if f.endswith('/README.md'):
            continue
        sys.stdout.write(f'=== {f}\n' + git('show', f'{c}:{f}'))


if __name__ == '__main__':
    main()
