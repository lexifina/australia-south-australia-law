"""Appends South Australian Acts read from the open data on data.sa.gov.au to this repository, append-only: nothing
already published is rewritten; new commits go on top of main.

    python -X utf8 saappend.py plan   --docs-date YYYY-MM-DD                (what would be appended; writes .cache/open-plan.json)
    python -X utf8 saappend.py append --docs-date YYYY-MM-DD --acts N [--repo PATH]   (the next N Acts of the plan)

The open data (see saopen.py): the Legislative Database of 17 November 2017 (every Act then in operation, as RTF)
and every fortnightly Legislative Database Update Package (XML) since (each Act substituted or added, as a whole
version in exchange XML, with the day it took effect, `first.valid.date`). An Act is identified by its year and
number, so a renamed Act stays one Act. Its versions are the 2017 RTF (if it was in operation then) and every XML
version, one per first day (the latest publication wins), up to --docs-date.

Which Acts: those in force (on the Acts A-Z list read with the rest of this record) that main does not hold yet.
Each run appends whole Acts, in A-Z order: an Act's first version, then one commit per amending Act per day, credited
and dated exactly as build.py does for the website's versions (the history table of each version names the Act that
changed each provision, with its commencement). Because the Acts appended here are added after the Acts already on
main, their commits come after commits with later dates: the In-force trailer, not the commit order, gives each
commit's day, and tools/in_force_on.py reads an Act's text on a day from the commits that touch that Act only.

Author date: noon in Adelaide on the In-force day, as build.py. Committer date: the published head's committer time
plus one second per appended commit, so committer dates never go backwards along main. Existing tags are never
moved; each appended version gets its tag `<act>/<first day>` on the commit that holds it.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build as B  # noqa: E402
import sacache  # noqa: E402
import saopen  # noqa: E402
import saxml  # noqa: E402

PLAN = sacache.CACHE / 'open-plan.json'
DEFAULT_REPO = HERE.parents[2] / 'australia-south-australia-law-build'


def norm_title(s: str) -> str:
    return re.sub(r'\s+', ' ', s.replace('&#x00A0;', ' ').replace('\u00a0', ' ')).strip()


# ---------------------------------------------------------------- reading the open data

def _parse(job):
    kind, a, b = job
    if kind == 'xml':
        return job, saxml.read(saopen.read_member(a, b))
    return job, B.read_version(saopen.read_base(a))


def source_of(job, ix_url: dict) -> str:
    kind, a, b = job
    if kind == 'xml':
        return f'{ix_url[a]}#A.zip/{b}'
    return f'{saopen.BASE_URL}#{a}'


def open_acts(workers: int = 2) -> list[dict]:
    """Every Act in the open data: identity (year, number), its versions (parsed), its titles."""
    ix = saopen.load_index()
    ix_url = {f['pkg']: f['url'] for f in ix['files']}
    acts: dict[tuple, dict] = {}
    for b in ix['base']:
        k = (b['year'], b['number'])
        acts.setdefault(k, {'key': k, 'cands': []})['cands'].append(
            {'job': ('rtf', b['member'], ''), 'pub': saopen.BASE_DAY, 'title': b['folder'], 'sha256': b['sha256']})
    for f in ix['files']:
        if f.get('doc_class') not in (None, 'act'):
            continue
        # identity from the file name (year.number.xml), as for the RTF files: the XML's own number attribute is
        # occasionally wrong (Water Industry Act 2012: file 2012.10, attribute 9)
        m = re.match(r'(\d{4})\.([^.]+)\.', f['member'].rsplit('/', 1)[-1])
        k = (m.group(1), m.group(2)) if m else (f['year'], f['number'])
        acts.setdefault(k,{'key': k, 'cands': []})['cands'].append(
            {'job': ('xml', f['pkg'], f['member']), 'pub': f['pkg_day'], 'title': f['folder'], 'sha256': f['sha256']})
    jobs = [c['job'] for a in acts.values() for c in a['cands']]
    parsed = {}
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        for job, doc in ex.map(_parse, jobs, chunksize=8):
            parsed[job] = doc
    out = []
    for a in acts.values():
        vs = []
        for c in a['cands']:
            doc = parsed[c['job']]
            day = doc['version_day']
            if not day:
                continue
            title = norm_title(doc.get('title') or c['title'])
            vs.append({'from': day, 'pub': c['pub'], 'doc': doc, 'job': list(c['job']), 'title': title, 'rtf': source_of(c['job'], ix_url),
                       'kind': c['job'][0], 'sha256': c['sha256'], 'name': 'open data', 'sub': ''})
        # one version per first day: the latest publication wins
        vs.sort(key=lambda v: (v['from'], v['pub']))
        uniq = {}
        for v in vs:
            uniq[v['from']] = v
        a['versions'] = [uniq[d] for d in sorted(uniq)]
        if a['versions']:
            last = max(a['versions'], key=lambda v: (v['pub'], v['from']))
            a['title'] = last['title']
            a['titles'] = sorted({v['title'] for v in a['versions']})
            out.append(a)
    return out


def ceased_titles() -> dict[str, str]:
    """{Act title: package day} for every Act an instruction sheet says has ceased."""
    ix = saopen.load_index()
    out = {}
    for day, s in sorted(ix['sheets'].items()):
        for t in s['ceased']:
            out[norm_title(t)] = day
    return out


def current_titles() -> dict[str, dict]:
    """The Acts in force on the Acts A-Z list read on 2026-10-29 (title -> entry)."""
    acts = json.loads((sacache.CACHE / 'acts.json').read_text('utf-8'))
    return {norm_title(a['title']): a for a in acts if 'Ceased' not in a['note']}


# ---------------------------------------------------------------- what main holds

def main_acts(repo: Path) -> set[str]:
    out = B.git(repo, 'ls-tree', '-d', '--name-only', 'main')
    return {l for l in out.split('\n') if l and l not in ('tools', 'data')}


def act_rows(repo: Path, ref: str = 'main') -> dict[str, list[tuple[str, str]]]:
    """{act folder: [(commit, In-force day)], newest commit first} from one walk of main (the commits that touch the
    folder), as tools/in_force_on.py and gitlaw_common/check.py read them."""
    rows: dict[str, list] = {}
    out = B.git(repo, 'log', '--no-merges', '--no-renames', '--name-only',
                '--format=%x00%H%x09%(trailers:key=In-force,valueonly,separator=)', ref)
    for rec in out.split('\x00')[1:]:
        head, _, names = rec.partition('\n')
        h, _, d = head.partition('\t')
        d = d.strip()
        if not d:
            continue
        for a in {n.split('/')[0] for n in names.splitlines() if '/' in n}:
            rows.setdefault(a, []).append((h, d))
    return rows


def state_on(rows: list[tuple[str, str]], day: str) -> str | None:
    """The commit that holds an Act's text on a day: of the commits touching the Act, the newest (in commit order)
    whose In-force day is on or before the day."""
    best = None
    for h, d in rows:          # newest first
        if d <= day and (best is None or d > best[1]):
            best = (h, d)
    return best[0] if best else None


# ---------------------------------------------------------------- the plan

# Acts left out for now, with the reason (read by hand)
HOLD = {'whyalla-steel-works-act-1958': 'its 2017 version is RTF, whose reader (sartf.py, shared with the Acts first '
         'published) leaves out a subheading the XML prints (APPENDIX D, LAND TO BE DISPOSED); the change of reader would '
         'show as a change of law'}


def tkey(title: str) -> str:
    """A title's letters and digits: "Children's" and "Childrens", or a dash and a space, match."""
    return re.sub(r'[^a-z0-9]', '', norm_title(title).lower())


def folder_of(title: str) -> str:
    """The folder of an Act: its title in lower case, apostrophes dropped, other runs of signs as one hyphen (the
    site's own folder names, "governors-pensions-act-1976")."""
    return B.slug(re.sub("['\u2019]", '', norm_title(title)))


def make_plan(repo: Path, docs_date: str, workers: int) -> dict:
    acts = open_acts(workers)
    cur = {tkey(t): t for t in current_titles()}
    ceased = ceased_titles()
    held_folders = main_acts(repo)
    # the Acts of the first build, by their A-Z titles and folders
    first = B.select_acts(acts_file=str(sacache.CACHE / 'pinned-acts.json'))
    first_keys = {tkey(x['title']) for x in first}
    plan, published, notcurrent, held_acts = [], [], [], []
    for a in acts:
        keys = {tkey(t) for t in a['titles']}
        folders = {folder_of(t) for t in a['titles']} | {B.slug(t) for t in a['titles']}
        if keys & first_keys or folders & held_folders:
            published.append({'key': a['key'], 'title': a['title'],
                              'folder': sorted(folders & held_folders)[0] if folders & held_folders else None})
            continue
        if tkey(a['title']) not in cur:
            notcurrent.append({'key': a['key'], 'title': a['title'], 'ceased': ceased.get(a['title'])})
            continue
        slug = folder_of(a['title'])
        if slug in HOLD:
            held_acts.append({'slug': slug, 'title': a['title'], 'why': HOLD[slug]})
            continue
        vs = [v for v in a['versions'] if v['from'] <= docs_date]
        later = [v['from'] for v in a['versions'] if v['from'] > docs_date]
        plan.append({'key': list(a['key']), 'slug': slug, 'title': a['title'], 'titles': a['titles'],
                     'versions': [{'from': v['from'], 'pub': v['pub'], 'kind': v['kind'], 'rtf': v['rtf'], 'job': v['job'],
                                   'sha256': v['sha256'], 'title': v['title']} for v in vs],
                     'later': later})
    plan.sort(key=lambda p: p['slug'])
    dup = sorted({p['slug'] for p in plan if sum(1 for q in plan if q['slug'] == p['slug']) > 1})
    if dup:
        raise SystemExit(f'two Acts share a folder: {dup[:5]}')
    out = {'docs_date': docs_date, 'acts': plan, 'published': published, 'not_current': notcurrent,
           'held': held_acts}
    PLAN.write_text(json.dumps(out, indent=0, sort_keys=True) + '\n', 'utf-8')
    return out


# ---------------------------------------------------------------- messages (build.message, with the open data's words)

def start_text(t: dict, v: dict, day: str) -> list[str]:
    if v['kind'] == 'rtf':
        where = (f'the version in the Legislative Database of 17 November 2017 on data.sa.gov.au (every Act then in '
                 f'operation, as RTF), the earliest version in this record')
    else:
        where = (f'the first version of it in the Legislative Database Update Packages (XML) on data.sa.gov.au '
                 f'(package of {v["pub"]})')
    return B.wrap(f'The {B.short_title(t)} as in force on {day}: {where}. Amendments in force before {day} are in '
                  'this text and not separated out; the legislative history at the end of the version names them.')


def message(g: dict, names: dict) -> tuple[tuple[str, str], str, list[str]]:
    if g['kind'] != 'start':
        author, msg, files = B.message(g, names)
        msg = msg.replace('\nVersion: ', '\nSource: ')
        return author, msg, files
    titles = sorted(g['titles'])
    t = g['titles'][titles[0]]
    v = t['versions'][0]
    day = g['day']
    princ = list(v['doc']['principal'])
    author = B.PUBLISHER
    subj = f'{B.short_title(t)}: the Act as in force on {day}'
    body = start_text(t, v, day)
    if len(princ) == 1 and day in princ[0]['dates']:
        n = princ[0]
        author = (f'{n["title"]} (No. {n["no"]} of {n["year"]})', f'act-{n["year"]}-{n["no"]}@legislation-sa.invalid')
        subj = f'{B.short_title(t)}: the Act as it took effect'
        body = B.wrap(f'The {B.short_title(t)} as it took effect on {day}: no amending Act is in its history.')
    body += [''] + B.wrap('Appended from the South Australian open data after the Acts first published here: this '
                          'commit comes after commits with later In-force days. The In-force trailer gives its day.')
    listing = textwrap.wrap(f'{B.short_title(t)}: ' + ', '.join(B.unit_label(k) for k in sorted(v['doc']['order'])),
                            100, subsequent_indent='  ', break_on_hyphens=False, break_long_words=False)
    trailers = ['Kind: baseline' if author == B.PUBLISHER else 'Kind: Act', f'In-force: {day}', f'Source: {v["rtf"]}']
    if len(subj) > 72:
        subj = subj[:69].rstrip() + '...'
    affecting = sorted(g['files'])
    trailers += [f'Affecting: {p}' for p in affecting]
    msg = subj + '\n\n' + '\n'.join(['Changes:'] + listing + [''] + body).strip('\n') + '\n\n' + '\n'.join(trailers) + '\n'
    return author, msg, affecting


# ---------------------------------------------------------------- appending

def letters(s: str | None) -> str:
    """The text without white space and table bars, a non-breaking hyphen read as a hyphen: punctuation and words
    still count."""
    return re.sub(r'[\s│]+', '', (s or '').replace('‑', '-'))


def carry_forward(vs: list[dict]) -> None:
    """The 2017 version is RTF and later ones XML; the two readers print a few things differently (a space, a table
    bar) with the same words. A unit equal to the version before's except in white space and table bars keeps the
    version before's text, so a change of source format is never committed as a change of law. The units kept are
    listed in the version's 'carried' (checked the same way, not byte for byte, in check_open.py)."""
    for prev, cur in zip(vs, vs[1:]):
        pu, cu = prev['doc']['units'], cur['doc']['units']
        units = dict(cu)
        carried = []
        for k, txt in cu.items():
            if k in pu and pu[k] != txt and letters(pu[k]) == letters(txt):
                units[k] = pu[k]
                carried.append(k)
        cur['doc'] = {**cur['doc'], 'units': units}
        cur['carried'] = carried


def titles_for(entries: list[dict], workers: int) -> list[dict]:
    """build.py's title dicts for the planned Acts, with every version parsed again from the open data."""
    jobs = [tuple(v['job']) for e in entries for v in e['versions']]
    parsed = {}
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        for job, doc in ex.map(_parse, jobs, chunksize=4):
            parsed[job] = doc
    out = []
    for e in entries:
        vs = [{**v, 'doc': parsed[tuple(v['job'])], 'name': 'open data', 'sub': ''} for v in e['versions']]
        carry_forward(vs)
        out.append({'act': {'title': e['title'], 'path': e['slug']}, 'slug': e['slug'], 'versions': vs,
                    'pdf_only': [], 'missing': [], 'key': e['key'], 'later': e['later']})
    return out


def append(repo: Path, docs_date: str, n: int, workers: int, docs=None, dry_run: bool = False,
           layered: bool = False) -> dict:
    plan = json.loads(PLAN.read_text('utf-8'))
    if plan['docs_date'] != docs_date:
        raise SystemExit(f'the plan is for {plan["docs_date"]}; run plan again')
    if B.git(repo, 'status', '--porcelain', '--untracked-files=no').strip():
        raise SystemExit(f'{repo} has uncommitted changes')
    held = main_acts(repo)
    todo = [e for e in plan['acts'] if e['slug'] not in held][:n]
    if not todo:
        return {'appended': 0}
    titles = titles_for(todo, workers)
    names = B.names_of(titles)
    groups = B.plan(titles)
    if layered:
        # breadth first: every Act's first version (A-Z), then every later change of all of them, in day order
        starts = sorted((g for g in groups if g['kind'] == 'start'), key=lambda g: min(g['titles']))
        groups = starts + [g for g in groups if g['kind'] != 'start']
    head = B.git(repo, 'rev-parse', 'main').strip()
    ct, tz = B.git(repo, 'log', '-1', '--format=%ct %ci', 'main').split()[0::3]
    ct = int(ct)
    existing = set(B.git(repo, 'tag', '--list').split())
    out = io.BytesIO()

    def data(b: bytes):
        out.write(b'data %d\n' % len(b))
        out.write(b)
        out.write(b'\n')
    seq = [0]
    mark = 0
    parent = head
    act_marks: dict[str, list] = {}
    kinds = {}
    for g in groups:
        author, msg, files = message(g, names)
        mark += 1
        ct += 1
        out.write(f'commit refs/heads/main\nmark :{mark}\n'.encode())
        out.write(f'author {author[0]} <{author[1]}> {B.commit_date(g["day"], seq)}\n'.encode('utf-8'))
        out.write(f'committer {B.COMMITTER[0]} <{B.COMMITTER[1]}> {ct} {tz}\n'.encode('utf-8'))
        data(msg.encode('utf-8'))
        out.write((f'from {parent}\n' if mark == 1 else f'from :{mark - 1}\n').encode())
        for p in files:
            txt = g['files'][p]
            if txt is None:
                out.write(b'D ' + p.encode('utf-8') + b'\n')
            else:
                out.write(b'M 100644 inline ' + p.encode('utf-8') + b'\n')
                data(txt.encode('utf-8'))
        out.write(b'\n')
        for s in g['titles']:
            act_marks.setdefault(s, []).append((g['day'], mark))
        kinds[g['kind']] = kinds.get(g['kind'], 0) + 1
    # a version's tag: of the commits that touch the Act, the one tools/in_force_on.py reads for the version's first
    # day (the latest In-force day on or before it; the later commit if two share it)
    tags = {}
    for t in titles:
        ms = act_marks.get(t['slug'], [])
        for v in t['versions']:
            best = None
            for d, m in ms:
                if d <= v['from'] and (best is None or (d, m) > best):
                    best = (d, m)
            name = f'{t["slug"]}/{v["from"]}'
            if best and name not in existing:
                tags[name] = best[1]
    for name, m in sorted(tags.items()):
        out.write(f'reset refs/tags/{name}\nfrom :{m}\n\n'.encode('utf-8'))
    out.write(b'done\n')
    stats = {'head': head, 'acts': [t['slug'] for t in titles], 'commits': len(groups), 'kinds': kinds,
             'versions': sum(len(t['versions']) for t in titles), 'tags': len(tags),
             'unexplained': sum(1 for g in groups if g['kind'] == 'unexplained')}
    if dry_run:
        return stats
    r = subprocess.run(['git', 'fast-import', '--quiet', '--done'], cwd=repo, input=out.getvalue(), capture_output=True)
    if r.returncode:
        raise SystemExit(f'git fast-import failed: {r.stderr.decode(errors="replace")}')
    B.git(repo, 'reset', '-q', '--hard', 'main')
    if docs:
        when = f'{ct + 1} {tz}'
        docs(repo, titles, groups, names, docs_date, stats)
        B.git(repo, 'add', '-A')
        env = {**os.environ, 'GIT_AUTHOR_NAME': B.COMMITTER[0], 'GIT_AUTHOR_EMAIL': B.COMMITTER[1],
               'GIT_AUTHOR_DATE': B.commit_date(docs_date, [0]), 'GIT_COMMITTER_NAME': B.COMMITTER[0],
               'GIT_COMMITTER_EMAIL': B.COMMITTER[1], 'GIT_COMMITTER_DATE': when}
        B.git(repo, 'commit', '-q', '--cleanup=verbatim', '-F', '-', env=env,
              input=(f'Documentation, tables and tools: {len(titles)} Acts appended from the open data\n\nNo law '
                     f'text changes.\n\nIn-force: {docs_date}\nKind: documentation\n').encode('utf-8'))
    new = B.git(repo, 'rev-parse', 'main').strip()
    if subprocess.run(['git', 'merge-base', '--is-ancestor', head, new], cwd=repo).returncode:
        raise SystemExit('internal: the published head is not an ancestor of the new main')
    stats['main'] = new
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('what', choices=['plan', 'append'])
    ap.add_argument('--repo', default=str(DEFAULT_REPO))
    ap.add_argument('--docs-date', required=True)
    ap.add_argument('--acts', type=int, default=25)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-docs', action='store_true')
    ap.add_argument('--layered', action='store_true', help='first versions of all the Acts, then all their changes')
    a = ap.parse_args()
    if a.what == 'plan':
        p = make_plan(Path(a.repo), a.docs_date, a.workers)
        print(json.dumps({'to_append': len(p['acts']), 'versions': sum(len(e['versions']) for e in p['acts']),
                          'published': len(p['published']), 'not_current': len(p['not_current'])}))
        return
    docs = None
    if not a.no_docs:
        from saopen_docs import write_docs
        docs = write_docs
    print(json.dumps(append(Path(a.repo), a.docs_date, a.acts, a.workers, docs, a.dry_run, a.layered), sort_keys=True))


if __name__ == '__main__':
    main()
