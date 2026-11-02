"""What a South Australian Act, or one of its provisions, said on a day.

    python tools/in_force_on.py 2015-06-01 constitution-act-1934 s-028
    python tools/in_force_on.py 2015-06-01 constitution-act-1934          (the whole Act)
    python tools/in_force_on.py 2015-06-01 constitution-act-1934 --commit (the commit that holds that day's text)

Reading rule. Of the commits on main that touch the Act's folder and are not corrections, take the one with the
latest In-force day on or before the day (the newest such commit if two share it). Then apply, file by file and in
commit order, each correction commit (Kind: correction) whose Corrects range holds the day: its Files trailers name
the files it corrects, read from its own tree (a listed file its tree lacks is removed); an open range ("<from>..")
ends the day before the Act's next ordinary commit with a later In-force day. The commits are not all in date order:
Acts added later from the open data were appended after the Acts first published, so the trailer, not the position,
gives each commit's day. A provision may be given as a file name (s-028, sch-1/cl-003) or as "s 28".
"""
import datetime as dt
import re
import subprocess
import sys

FS, RS = '\x1f', '\x1e'


def git(*args: str, repo=None) -> str:
    return subprocess.run(['git', *args], cwd=repo, capture_output=True, check=True).stdout.decode('utf-8')


def history(act: str, ref: str = 'main', repo=None) -> list[tuple[str, str, bool]]:
    """(commit, In-force day, is a correction), newest commit first, of the commits touching act/."""
    out = git('log', '--no-merges', f'--format=%H{FS}%(trailers:key=In-force,valueonly,separator=)'
              f'{FS}%(trailers:key=Kind,valueonly,separator=,){RS}', ref, '--', act + '/', repo=repo)
    rows = []
    for rec in out.split(RS):
        parts = rec.strip('\n').split(FS)
        if len(parts) == 3 and parts[1].strip():
            rows.append((parts[0], parts[1].strip(), 'correction' in [k.strip() for k in parts[2].split(',')]))
    return rows


def corrections(act: str, ref: str = 'main', repo=None) -> list[dict]:
    """The correction commits for the Act, oldest first: range and {file: blob id or None}."""
    out = git('log', '--no-merges', '--reverse', '--grep=^Kind: *correction',
              f'--format=%H{FS}%(trailers:key=Corrects,valueonly,separator={RS})'
              f'{FS}%(trailers:key=Files,valueonly,separator={RS})\x1d', ref, repo=repo)
    fixes = []
    for rec in out.split('\x1d'):
        parts = rec.strip('\n').split(FS)
        if len(parts) != 3 or not parts[0]:
            continue
        h, cor, files = parts
        rng = None
        for line in cor.split(RS):
            p = line.split()
            if len(p) == 2 and p[0] == act and '..' in p[1]:
                a, _, b = p[1].partition('..')
                rng = (a, b or None)
        if rng is None:
            continue
        listed = [f.strip() for f in files.split(RS) if f.strip().startswith(act + '/')]
        tree = tree_ids(h, act, repo)
        fixes.append({'commit': h, 'range': rng, 'files': {f: tree.get(f) for f in listed}})
    return fixes


def tree_ids(commit: str, act: str, repo=None) -> dict[str, str]:
    out = {}
    for rec in git('-c', 'core.quotePath=false', 'ls-tree', '-r', '-z', commit, '--', act + '/', repo=repo).split('\0'):
        meta, _, p = rec.partition('\t')
        if p and meta.split()[1] == 'blob' and not p.endswith('/README.md'):
            out[p] = meta.split()[2]
    return out


def day_before(day: str) -> str:
    return (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()


def state_on(day: str, act: str, ref: str = 'main', repo=None, rows=None, fixes=None):
    """({file: blob id} of the Act on the day, or None; the ordinary commit chosen; the corrections applied)."""
    rows = history(act, ref, repo) if rows is None else rows
    ordinary = [(h, d) for h, d, c in rows if not c]
    best = None
    for h, d in ordinary:          # newest commit first
        if d <= day and (best is None or d > best[1]):
            best = (h, d)
    files = tree_ids(best[0], act, repo) if best else None
    fixes = corrections(act, ref, repo) if fixes is None else fixes
    used = []
    for f in fixes:
        a, b = f['range']
        if b is None:
            b = day_before(min((d for _h, d in ordinary if d > a), default='9999-12-31'))
        if a <= day <= b:
            files = {} if files is None else files
            for p, o in f['files'].items():
                if o is None:
                    files.pop(p, None)
                else:
                    files[p] = o
            used.append(f['commit'])
    return files, (best[0] if best else None), used


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
    files, c, used = state_on(day, act)
    if not files:
        raise SystemExit(f'{act}: not in force on {day} in this record')
    if '--commit' in sys.argv:
        print(git('show', '-s', '--format=%H%n%an%n%n%B', c))
        for u in used:
            print(git('show', '-s', '--format=Corrected by %H: %s', u))
        return
    if len(a) >= 3:
        path = f'{act}/{key_of(a[2])}.md'
        if path not in files:
            raise SystemExit(f'{path}: not in force on {day}')
        sys.stdout.write(git('cat-file', 'blob', files[path]))
        return
    for f in sorted(files):
        sys.stdout.write(f'=== {f}\n' + git('cat-file', 'blob', files[f]))


if __name__ == '__main__':
    main()
