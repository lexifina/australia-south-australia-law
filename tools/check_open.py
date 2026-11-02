"""Checks every version of every Act on main, choosing commits as tools/in_force_on.py does (per Act, by In-force).

    python -X utf8 check_open.py [--repo PATH] [--today D] [--open-only] [--workers 2]

For each Act (the first 267 from the website's RTF versions, the rest from the open data on data.sa.gov.au):
1. on each version's first day, the Act's files in the commit that holds that day equal the version as read from its
   source, file by file; on the day before, the Act reads as the version before it (or not at all before its first), unless a change
   dated by its commencement falls between the two versions;
2. its tag `<act>/<first day>` holds the same files;
3. (open-data Acts) the letters and digits of the files tile those of the source as read by a separate reader that
   shares no code with the build: `xml_letters` below (regular expressions over the raw XML) for XML versions, and
   check_words.rtf_letters for the 2017 RTF versions;
and main has no merge commit, no Act folder the check does not know, and committer dates never go backwards.
Writes the result to verification.json ("open").
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build as B  # noqa: E402
import check_words as W  # noqa: E402
import saappend as AP  # noqa: E402
import saopen  # noqa: E402
import sacache  # noqa: E402
import in_force_on as IFO  # noqa: E402
import hashlib  # noqa: E402


def blob_oid(text: str) -> str:
    b = text.encode('utf-8')
    return hashlib.sha1(('blob %d' % len(b)).encode() + bytes(1) + b).hexdigest()


# ---------------------------------------------------------------- the separate XML reader (letters and digits)

def xml_letters(raw: bytes) -> str:
    t = raw.decode('utf-8', 'replace')
    title = html.unescape(html.unescape(re.search(r'<exdoc [^>]*?title="([^"]*)"', t).group(1)))
    start = t.find('<content type="front"')
    if start < 0:
        start = t.find('<content type="body"')
    ends = [i for i in (t.find('<content type="editorial"', start), t.find('<content type="history"', start),
                        t.find('<level type="leghistory"', start)) if i >= 0]
    body = t[start:min(ends)] if ends else t[start:]
    # left out of the text, as the RTF leaves them out: a form's heading (printed with its drawing) and footnote
    # markers (superscript numbers)
    body = re.sub(r'(<level type="form"[^>]*>(?:\s*<parentattributes>.*?</parentattributes>)?\s*)<head>.*?</head>',
                  r'\1', body, flags=re.S)
    body = re.sub(r'<sup>\s*\d+\s*</sup>', '', body)
    body = re.sub(r'<[^>]+>', ' ', body)
    return W.letters('South Australia') + W.letters(title) + W.letters(html.unescape(body)) + 'legislativehistory'


def words_job(arg):
    label, kind, job, files = arg
    raw = saopen.read_member(job[1], job[2]) if kind == 'xml' else saopen.read_base(job[1])
    src = xml_letters(raw) if kind == 'xml' else W.rtf_letters(raw)
    title = [W.letters(l) for l in files.pop('title.md', '').splitlines()]
    pieces = []
    for name, txt in files.items():
        if name == 'headings.md':
            pieces += [W.letters(re.sub(r' \[[a-z]+(-[0-9A-Za-z]+)?\]$', '', l)) for l in txt.splitlines()]
        else:
            pieces.append(W.letters(txt))
    ok, detail = W.tile(src, title, pieces)
    return label, ok, detail


# ---------------------------------------------------------------- git

def tree(cat, rev: str, folder: str) -> dict[str, bytes]:
    """{path: bytes} of every file under folder/ at rev (README.md excepted)."""
    out = {}

    def walk(spec, prefix):
        data = cat.read(spec)
        if data is None:
            return
        i = 0
        while i < len(data):
            sp = data.index(b' ', i)
            nul = data.index(b'\0', sp)
            mode, name, sha = data[i:sp], data[sp + 1:nul].decode('utf-8'), data[nul + 1:nul + 21].hex()
            i = nul + 21
            if mode == b'40000':
                walk(sha, prefix + name + '/')
            else:
                out[prefix + name] = cat.read(sha)
    walk(f'{rev}:{folder}', folder + '/')
    return {k: v for k, v in out.items() if not k.endswith('/README.md')}


class Cat:
    def __init__(self, repo):
        self.p = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, spec):
        self.p.stdin.write(spec.encode('utf-8') + b'\n')
        self.p.stdin.flush()
        head = self.p.stdout.readline().split()
        if len(head) < 3:
            return None
        data = self.p.stdout.read(int(head[2]))
        self.p.stdout.read(1)
        return data


def prev_day(day: str) -> str:
    return (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(AP.DEFAULT_REPO))
    ap.add_argument('--today', default='9999-12-31')
    ap.add_argument('--open-only', action='store_true', help='skip the first 267 Acts (checked by check_repo.py)')
    ap.add_argument('--workers', type=int, default=2)
    a = ap.parse_args()
    repo = Path(a.repo)
    rows = AP.act_rows(repo)
    held = AP.main_acts(repo)
    tags = dict(l.split(' ') for l in B.git(repo, 'for-each-ref', '--format=%(refname:short) %(objectname)',
                                            'refs/tags').splitlines() if l)
    titles = []
    if not a.open_only:
        first = B.load_acts(B.select_acts(acts_file=str(sacache.CACHE / 'pinned-acts.json')), a.workers)
        for t in first:
            t['source'] = 'website'
        titles += first
    plan = json.loads(AP.PLAN.read_text('utf-8'))
    entries = [e for e in plan['acts'] if e['slug'] in held]
    opened = AP.titles_for(entries, a.workers)
    for t in opened:
        t['source'] = 'open'
    titles += opened
    known = {t['slug'] for t in titles}
    cat = Cat(repo)
    # corrections (Kind: correction): the corrected files of a published version, from the official XML
    cp = sacache.CACHE / 'open-corrections.json'
    fixes_want = {}
    if cp.exists():
        for c in json.loads(cp.read_text('utf-8'))['corrections']:
            fixes_want[f'{c["act"]}/{c["from"]}'] = c['files']
    bad, words = [], []
    nver = nfiles = between = ncorr = 0

    def ids(units: dict, slug: str, label: str | None = None) -> dict[str, str]:
        out = {f'{slug}/{B.file_name(k)}': blob_oid(txt) for k, txt in units.items()}
        for p, txt in (fixes_want.get(label) or {}).items():
            if txt is None:
                out.pop(p, None)
            else:
                out[p] = blob_oid(txt)
        return out
    for t in titles:
        slug = t['slug']
        hist = IFO.history(slug, repo=repo)
        fixes = IFO.corrections(slug, repo=repo) if any(c for _h, _d, c in hist) else []
        ordinary = [d for _h, d, c in hist if not c]
        vs = [v for v in t['versions'] if v['from'] <= a.today]
        for i, v in enumerate(vs):
            label = f'{slug}/{v["from"]}'
            want = ids(v['doc']['units'], slug, label)
            ncorr += label in fixes_want
            nver += 1
            nfiles += len(want)
            have, rev, _used = IFO.state_on(v['from'], slug, repo=repo, rows=hist, fixes=fixes)
            if have != want:
                bad.append(f'{label}: the text on its first day differs')
                continue
            before, _r, _u = IFO.state_on(prev_day(v['from']), slug, repo=repo, rows=hist, fixes=fixes)
            if i == 0:
                if before is not None:
                    bad.append(f'{label}: the Act reads as in force the day before its first version')
            elif any(vs[i - 1]['from'] < d < v['from'] for d in ordinary):
                between += 1   # changes dated by their commencement between the two versions: no version to compare
            else:
                pl = f'{slug}/{vs[i - 1]["from"]}'
                if before != ids(vs[i - 1]['doc']['units'], slug, pl):
                    bad.append(f'{label}: the day before does not read as the version of {vs[i - 1]["from"]}')
            if label not in tags:
                bad.append(f'{label}: no tag')
            elif label not in fixes_want and tags[label] != rev and IFO.tree_ids(tags[label], slug, repo) != want:
                bad.append(f'{label}: the tag holds other text')
            if t['source'] == 'open':
                files = {k: txt for k, txt in v['doc']['units'].items()}
                files = {B.file_name(k): txt for k, txt in files.items()}
                words.append((label, v['kind'], tuple(v['job']), files))
    unknown = sorted(held - known) if not a.open_only else []
    wbad = []
    with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
        for label, ok, detail in ex.map(words_job, words, chunksize=8):
            if not ok:
                wbad.append(f'{label}: {detail}')
    merges = B.git(repo, 'rev-list', '--merges', 'main').split()
    cts = [int(x) for x in B.git(repo, 'log', '--first-parent', '--reverse', '--format=%ct', 'main').split()]
    back = sum(1 for x, y in zip(cts, cts[1:]) if y < x)
    rec = {'acts': len(titles), 'open_acts': len(opened), 'versions': nver, 'files': nfiles, 'mismatches': len(bad), 'day_before_skipped': between,
           'examples': bad[:12], 'words_checked': len(words), 'words_failed': len(wbad), 'words_examples': wbad[:8],
           'unknown_folders': unknown[:10], 'merges': len(merges), 'committer_dates_backwards': back,
           'corrected_versions_checked': ncorr}
    print(json.dumps(rec, indent=1))
    p = HERE / 'verification.json'
    ver = json.loads(p.read_text('utf-8')) if p.exists() else {}
    ver['open'] = {k: rec[k] for k in ('acts', 'open_acts', 'versions', 'files', 'mismatches', 'words_checked',
                                       'words_failed', 'merges', 'committer_dates_backwards')}
    p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', 'utf-8', newline='\n')
    raise SystemExit(0 if not (bad or wbad or unknown or merges or back) else 1)


if __name__ == '__main__':
    main()
