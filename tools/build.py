"""Builds a git repository of South Australian Acts from the South Australian Legislation website, in which every
amending Act is one commit, dated the day it took effect.

    python -X utf8 build.py --today 2026-10-28 [--repo ../../../australia-south-australia-law] [--verify-rebuild]

The site (www.legislation.sa.gov.au, the Commissioner for Legislation Revision and Publication) keeps every
consolidated version of each Act with the period it was in force ("12 May 2023 - 17 April 2024"), as PDF and, for
versions from about 2003, as RTF. Each version ends with a legislative history: a table of the principal Act and its
amending Acts (assent, commencement) and a table, "Provisions amended", with one row per provision and amending Act
("s 6(1) amended by 40/2021 s 3(1) ... 4.11.2021").

We read every RTF version of every current Act, compare consecutive versions provision by provision, and credit each
change to the Act that is new in that provision's rows of the history table, on the commencement day the row gives.
A change that nothing explains, or that two Acts could explain, is never credited to an Act: it gets its own commit
by the publisher, labelled. The build reads only the retrieved files (see sacache.py); it never touches the network.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sacache  # noqa: E402
import sartf  # noqa: E402

COMMITTER = ('Lexifina', 'australia-south-australia-law@lexifina.com')
PUBLISHER = ('South Australian Legislation (Commissioner for Legislation Revision and Publication)',
             'publisher@legislation-sa.invalid')
MONTHS = {m: i + 1 for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
                                          'September', 'October', 'November', 'December'])}


# ---------------------------------------------------------------- dates and time zone

def _sun(y: int, m: int, last: bool = False, on_or_after: int = 1) -> dt.date:
    if last:
        d = (dt.date(y, m + 1, 1) if m < 12 else dt.date(y + 1, 1, 1)) - dt.timedelta(days=1)
        return d - dt.timedelta(days=(d.weekday() + 1) % 7)
    d = dt.date(y, m, on_or_after)
    return d + dt.timedelta(days=(6 - d.weekday()) % 7)


def adelaide_offset(day: dt.date) -> int:
    """Minutes ahead of UTC at noon on a day in Adelaide, written out because this machine has no tz database.
    Australian Central Standard Time is UTC+9:30; daylight saving time is UTC+10:30. Changes happen at 2:00 standard
    time, so noon on a change day already has the new offset. The rules (tz database, 'AS' rules for
    Australia/Adelaide): summer time began 1971-1985 on the last Sunday in October; 1986 on 19 October; 1987-2007 on
    the last Sunday in October; since 2008 on the first Sunday in October. It ended 1972 on 27 February; 1973-1985 on
    the first Sunday in March; 1986-1990 on the Sunday on or after 15 March; 1991 on 3 March; 1992 on 22 March; 1993
    on 7 March; 1994 on 20 March; 1995-2005 on the last Sunday in March; 2006 on 2 April; 2007 on the last Sunday in
    March; since 2008 on the first Sunday in April. (Wartime summer time, 1917 and 1942-1944, is before 1970 and so
    never used: earlier days are clamped.)"""
    y = day.year
    if y < 1971:
        return 570
    if y == 1971:
        end = None
    elif y == 1972:
        end = dt.date(1972, 2, 27)
    elif y <= 1985:
        end = _sun(y, 3)
    elif y <= 1990:
        end = _sun(y, 3, on_or_after=15)
    elif y in (1991, 1992, 1993, 1994):
        end = {1991: dt.date(1991, 3, 3), 1992: dt.date(1992, 3, 22), 1993: dt.date(1993, 3, 7),
               1994: dt.date(1994, 3, 20)}[y]
    elif y <= 2005:
        end = _sun(y, 3, last=True)
    elif y == 2006:
        end = dt.date(2006, 4, 2)
    elif y == 2007:
        end = _sun(y, 3, last=True)
    else:
        end = _sun(y, 4)
    if y == 1986:
        start = dt.date(1986, 10, 19)
    elif y <= 2007:
        start = _sun(y, 10, last=True)
    else:
        start = _sun(y, 10)
    if end is not None and day < end:
        return 630
    return 630 if day >= start else 570


def commit_date(day: str, seq: list[int]) -> str:
    """Noon in Adelaide. Git cannot record a time before 1970: such days become 1 January 1970 (UTC), one second
    apart in order; the real day is in the In-force trailer."""
    d = dt.date.fromisoformat(day)
    if d.year < 1970:
        seq[0] += 1
        return f'{seq[0]} +0000'
    off = adelaide_offset(d)
    tz = dt.timezone(dt.timedelta(minutes=off))
    stamp = int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=tz).timestamp())
    return f'{stamp} +{off // 60:02d}{off % 60:02d}'


def long_date(s: str) -> str | None:
    m = re.match(r'(\d{1,2}) ([A-Z][a-z]+) (\d{4})$', s.strip())
    if not m or m.group(2) not in MONTHS:
        return None
    return dt.date(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1))).isoformat()


_DOT = re.compile(r'(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?![\d.])')


def dot_dates(s: str) -> list[str]:
    """Every d.m.yyyy date in a cell, except Gazette dates."""
    s = re.sub(r'Gazette\s+\d{1,2}\.\d{1,2}\.\d{4}', '', s)
    out = []
    for m in _DOT.finditer(s):
        try:
            out.append(dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat())
        except ValueError:
            pass
    return out


def next_day(day: str) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()


# ---------------------------------------------------------------- reading a version

_SOFT = re.compile('[\u00ad\u200b\u200c\u200d\ufeff]')
_SPACES = re.compile(r'[ \u00a0\u2002\u2003\u2009\n]+')


def render(text: str) -> str:
    text = _SOFT.sub('', text)
    parts = [p.strip(' \u00a0') for p in text.split('\t')]
    s = ' '.join(p for p in parts if p)
    return _SPACES.sub(' ', s).strip()


def num_key(n: str) -> str:
    m = re.match(r'(\d+)(.*)$', n)
    return f'{int(m.group(1)):03d}{m.group(2)}' if m else n


def slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-') or 'x'


HEAD_STYLE = re.compile(r'(chapter|part|division|subdivision)head', re.I)
SCHED_STYLE = re.compile(r'schedulehead|appendixhead', re.I)
CLAUSE_STYLE = re.compile(r'^clausehead', re.I)
NUM_HEAD = re.compile(r'^(\d+[A-Za-z]*)\s*[\u2014\u2013-]')
LEVEL_HEAD = re.compile(r'^(Chapter|Part|Division|Subdivision)\s+(\d+[A-Za-z]*)')
LEVELS = {'Chapter': 1, 'Part': 2, 'Division': 3, 'Subdivision': 4, 'Ch': 1, 'Pt': 2, 'Div': 3, 'Subdiv': 4}
LEVEL_ABBR = {'Chapter': 'Ch', 'Part': 'Pt', 'Division': 'Div', 'Subdivision': 'Subdiv'}
SCHED_TEXT = re.compile(r'^(Schedule|Appendix)(\s+\d+[A-Za-z]*)?\s*\u2014')
SCHED_HEAD = re.compile(r'^(Schedule|Appendix)\s*(\d+[A-Za-z]*)?', re.I)


def read_version(raw: bytes) -> dict:
    paras = sartf.paragraphs(raw)
    m = re.search(rb'Version: (\d{1,2}\.\d{1,2}\.\d{4})', raw)
    version_day = dot_dates(m.group(1).decode()) if m else []
    hist = next((i for i, p in enumerate(paras) if p[0].lower().startswith('leghistoryhead')), None)
    contents = next((i for i, p in enumerate(paras) if p[0].lower().startswith('contentshead')), None)
    if hist is None:
        # older versions write the heading without its style: the last plain "Legislative history" paragraph that
        # is followed by the history's own (styled) headings; the table of contents has the same words earlier
        hist = next((i for i in range(len(paras) - 1, (contents or 0), -1)
                     if render(paras[i][1]) == 'Legislative history' and not paras[i][2]
                     and any(p[0].lower().startswith('historyhead') for p in paras[i + 1:i + 40])), len(paras))
    # the table of contents ends with a link to the legislative history; the body starts after it
    start = contents if contents is not None else -1
    # (older versions do not make the entries links, so the entry is found by its text) and, in some versions, an
    # appendix entry after it
    toc_end = next((i for i in range(start + 1, hist) if paras[i][2] and render(paras[i][1]) == 'Legislative history'),
                   None)
    if toc_end is None and contents is not None:
        toc_end = next((i for i in range(start + 1, hist) if render(paras[i][1]) == 'Legislative history'
                        and not HEAD_STYLE.search(paras[i][0])), None)
    while toc_end is not None and toc_end + 1 < hist and re.match(r'Appendix\b', render(paras[toc_end + 1][1])) \
            and not SCHED_STYLE.search(paras[toc_end + 1][0]):
        toc_end += 1
    if toc_end is None:
        toc_end = next((i - 1 for i in range(start + 1, hist) if HEAD_STYLE.search(paras[i][0])
                        or CLAUSE_STYLE.search(paras[i][0]) or SCHED_STYLE.search(paras[i][0])), start)
    last_link = toc_end
    units: dict[str, list[str]] = {}
    order: list[str] = []

    def add(key: str):
        if key in units:
            n = 2
            while f'{key}-{n}' in units:
                n += 1
            key = f'{key}-{n}'
        units[key] = []
        order.append(key)
        return key

    title = add('title')
    for st, tx, lk in paras[:contents if contents is not None else 0]:
        r = render(tx)
        if r:
            units[title].append(r)
    headings = add('headings')
    path: list[str] = []
    paths: dict[str, str] = {}
    cur = title
    sched = None
    body = [(st, render(tx)) for st, tx, lk in paras[last_link + 1:hist]]
    body = [(st, r) for st, r in body if r]

    def text_follows(i: int) -> bool:
        """Is the paragraph after body[i] text (not a section, clause, heading or schedule)?"""
        if i + 1 >= len(body):
            return False
        st, r = body[i + 1]
        return not (CLAUSE_STYLE.search(st) or HEAD_STYLE.search(st) or SCHED_STYLE.search(st) or SCHED_TEXT.match(r))

    for i, (st, r) in enumerate(body):
        if SCHED_STYLE.search(st) or SCHED_TEXT.match(r):
            sm = SCHED_HEAD.match(r)
            base = ('sch' if sm and sm.group(1).lower() == 'schedule' else 'app') + (
                f'-{sm.group(2)}' if sm and sm.group(2) else '') if sm else slug(r.split('\u2014')[0])[:40]
            sched = base
            cur = add(f'{base}/schedule')
            units[cur].append(r)
            continue
        if HEAD_STYLE.search(st):
            if sched is not None and text_follows(i):
                # a heading in a schedule followed by text (not clauses): its own file, heading and text in order
                cur = add(f'{sched}/x-{slug(r)[:40]}')
                units[cur].append(r)
                continue
            units[headings].append(r if sched is None else f'{r} [{sched}]')
            hm = LEVEL_HEAD.match(r)
            if hm and sched is None:
                lvl = LEVELS[hm.group(1)]
                path = [p for p in path if LEVELS[p.split(' ')[0]] < lvl] + [f'{LEVEL_ABBR[hm.group(1)]} {hm.group(2)}']
            # text after a heading and before the next section (an editorial note, "Part 3 has expired") stays with
            # the heading, not with the section before it
            cur = headings
            continue
        if CLAUSE_STYLE.search(st):
            nm = NUM_HEAD.match(r)
            if sched is None:
                paths[f's-{num_key(nm.group(1))}' if nm else ''] = ' '.join(path)
                cur = add(f's-{num_key(nm.group(1))}' if nm else f'x-{slug(r)[:40]}')
            else:
                cur = add(f'{sched}/cl-{num_key(nm.group(1))}' if nm else f'{sched}/x-{slug(r)[:40]}')
            units[cur].append(r)
            continue
        units[cur].append(r)
    if not units[headings]:
        del units[headings]
        order.remove(headings)
    text = {k: '\n'.join(units[k]) + '\n' for k in order}
    principal, provisions = read_history(paras[hist:])
    return {'units': text, 'order': order, 'paths': paths, 'version_day': version_day[0] if version_day else None,
            'principal': principal, 'provisions': provisions, 'history_paras': len(paras) - hist}


def cells(tx: str) -> list[str]:
    parts = [render(c) for c in tx.split('\u2502')]
    if parts and parts[-1] == '':
        parts = parts[:-1]
    return parts


ACT_REF = re.compile(r'\b(\d+)/(\d{4})\b')


def read_history(paras: list) -> tuple[list[dict], list[dict]]:
    section = None
    principal = []
    provisions = []
    label = ''
    parent = ''
    for st, tx, lk in paras:
        # a history heading by its style, or, where the RTF's style table could not be read (the style shows as its
        # number, "s24"), by its words
        if st.lower().startswith('historyhead') or (re.fullmatch(r's\d+', st) and re.match(
                r'(Notes|Principal Act|Provisions amended|Legislation (repealed|amended) by|Transitional|Historical '
                r'versions)', render(tx))):
            t = render(tx).lower()
            section = 'principal' if t.startswith('principal act') else 'provisions' if t.startswith('provisions amended') else None
            continue
        if st != 'row' and '\u2502' not in tx:
            continue
        c = cells(tx)
        if section == 'principal' and len(c) >= 5:
            if c[0] == 'Year':
                continue
            principal.append({'year': c[0], 'no': c[1], 'title': c[2], 'assent': (dot_dates(c[3]) or [None])[0],
                              'commencement': c[4], 'dates': dot_dates(c[4])})
        elif section == 'provisions' and len(c) >= 3:
            if c[0] == 'Provision':
                continue
            if c[0]:
                label = c[0]
                if label_keys(label):
                    m = re.match(r'^(ss? \d+[A-Za-z]*|(?:Sch|App)(?:edule|endix)?(?: \d+[A-Za-z]*)?)', label)
                    parent = m.group(1) if m else ''
                elif parent:
                    # a definition or clause listed under its section or schedule
                    label = f'{parent} {label}'
            how = c[1]
            if not how:
                continue
            acts = [(int(n), int(y)) for n, y in ACT_REF.findall(how)]
            editorial = 'Legislation Revision and Publication Act' in how and not acts
            provisions.append({'label': label, 'how': how, 'acts': acts, 'editorial': editorial,
                               'dates': dot_dates(c[2])})
    return principal, provisions


def label_keys(label: str) -> list[tuple[str, str]]:
    """[('exact', key) | ('prefix', key prefix) | ('headings', '') | ('part', '') | ('title', '')] for a row label."""
    l = label.strip()
    m = re.match(r'^ss? (\d+[A-Za-z]*)', l)
    if m:
        out = [('exact', f's-{num_key(m.group(1))}')]
        rg = re.match(r'^ss (\d+[A-Za-z]*)\s*(?:\u2014|\u2013|-|to)\s*(\d+[A-Za-z]*)', l)
        if rg:
            out.append(('range', (f's-{num_key(rg.group(1))}', f's-{num_key(rg.group(2))}')))
        if l.startswith('ss '):
            body = re.split(r'\(', l[3:])[0] if False else l[3:]
            for n in re.findall(r'(?:^|,|and|&)\s*(\d+[A-Za-z]*)', body):
                k = ('exact', f's-{num_key(n)}')
                if k not in out:
                    out.append(k)
        return out
    m = re.match(r'^(Sch|App)(?:edule|endix)?\s*(\d+[A-Za-z]*)?(?:\s+(?:cl|clause)\s+(\d+[A-Za-z]*))?', l)
    if m:
        base = ('sch' if m.group(1) == 'Sch' else 'app') + (f'-{m.group(2)}' if m.group(2) else '')
        if m.group(3):
            return [('exact', f'{base}/cl-{num_key(m.group(3))}')]
        return [('prefix', base + '/')]
    if re.match(r'^(Ch|Pt|Div|Subdiv)\b', l):
        return [('part', '')]
    if re.match(r'^heading', l, re.I):
        return [('headings', '')]
    if re.match(r'^(Long title|Preamble|Title|Short title)', l, re.I):
        return [('title', '')]
    return []


def sec_sort(key: str) -> tuple:
    m = re.match(r's-(\d+)([A-Za-z]*)', key)
    return (int(m.group(1)), m.group(2).upper()) if m else (0, key)


def row_matches(row: dict, key: str) -> str | None:
    best = None
    for kind, k in label_keys(row['label']):
        if kind == 'exact' and k == key:
            return 'exact'
        if kind == 'range' and key.startswith('s-') and '/' not in key and sec_sort(k[0]) <= sec_sort(key) <= sec_sort(k[1]):
            return 'exact'
        if kind == 'prefix' and key.startswith(k):
            return 'exact'
        if kind in ('headings', 'part') and key == 'headings':
            return 'exact'
        if kind == 'title' and key == 'title':
            return 'exact'
        if kind == 'part':
            best = 'part'
    return best


# ---------------------------------------------------------------- an Act

def parse_range(sub: str) -> tuple[str | None, str | None]:
    m = re.match(r'\((\d{1,2} [A-Za-z]+ \d{4}) - (\d{1,2} [A-Za-z]+ \d{4})', sub)
    if m:
        return long_date(m.group(1)), long_date(m.group(2))
    return None, None


def act_versions(act: dict) -> dict | None:
    body = sacache.cached(sacache.act_page_url(act['path']))
    if not body:
        return None
    meta = sacache.act_meta(body)
    folder = meta['doc_root'].rstrip('/').rsplit('/', 1)[-1] if meta['doc_root'] else slug(act['title'])
    vs = []
    pdf_only = []
    for v in sacache.versions(body):
        rtf = next((l for l in v['links'] if l.endswith('.rtf')), None)
        frm, to = parse_range(v['sub'])
        row = {'name': v['name'], 'sub': v['sub'], 'from': frm, 'to': to, 'rtf': rtf,
               'pdf': next((l for l in v['links'] if l.endswith('.pdf')), None)}
        (vs if rtf else pdf_only).append(row)
    return {'act': act, 'slug': folder, 'versions': vs, 'pdf_only': pdf_only}


def _parse(url: str):
    raw = sacache.cached(url)
    if not raw:
        return url, None
    return url, read_version(raw)


def load_acts(acts: list[dict], workers: int) -> list[dict]:
    titles = [t for t in (act_versions(a) for a in acts) if t]
    # complete titles only: every RTF version cached, or given a final negative answer by the server (those versions
    # are left out and listed in t['missing'])
    ready = [t for t in titles if t['versions'] and any(sacache.cached(v['rtf']) for v in t['versions'])
             and all(sacache.cached(v['rtf']) or sacache.failed(v['rtf']) for v in t['versions'])]
    for t in ready:
        t['missing'] = [v for v in t['versions'] if not sacache.cached(v['rtf'])]
    urls = [v['rtf'] for t in ready for v in t['versions']]
    parsed = {}
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        for url, r in ex.map(_parse, urls, chunksize=4):
            parsed[url] = r
    out = []
    for t in ready:
        for v in t['versions']:
            v['doc'] = parsed[v['rtf']]
            if v['from'] is None:
                v['from'] = v['doc']['version_day']
        vs = [v for v in t['versions'] if v['from'] and v['doc']]
        vs.sort(key=lambda v: v['from'])
        # one version per start day (the newest publication wins)
        uniq = {}
        for v in vs:
            uniq[v['from']] = v
        t['versions'] = [uniq[d] for d in sorted(uniq)]
        if t['versions']:
            out.append(t)
    return out


# ---------------------------------------------------------------- attribution

def names_of(titles: list[dict]) -> dict:
    names = {}
    for t in titles:
        for v in t['versions']:
            for r in v['doc']['principal']:
                try:
                    k = (int(r['no']), int(r['year']))
                except ValueError:
                    continue
                nm = re.sub(r'\s+repealed by .*$', '', r['title']).strip()
                names.setdefault(k, {'title': nm, 'assent': r['assent'], 'dates': r['dates']})
    return names


def principal_dates(doc: dict) -> dict:
    out = {}
    for r in doc['principal']:
        try:
            out[(int(r['no']), int(r['year']))] = r['dates']
        except ValueError:
            pass
    return out


def rows_for(doc: dict, key: str, how: str = 'exact', path: str = '') -> list[dict]:
    out = []
    for r in doc['provisions']:
        m = row_matches(r, key)
        if m != how:
            continue
        if how == 'part':
            lab = re.match(r'^((?:(?:Ch|Pt|Div|Subdiv) [0-9A-Za-z]+ ?)+)', r['label'])
            if not lab or (' ' + lab.group(1).strip() + ' ') not in (' ' + path + ' '):
                continue
        out.append(r)
    return out


def explain(prev: dict, cur: dict, key: str) -> tuple:
    """(kind, x, day, evidence) for a unit that differs between two consecutive versions."""
    s0, s1 = prev['from'], cur['from']
    pd_, cd = prev['doc'], cur['doc']
    path = cd['paths'].get(key) or pd_['paths'].get(key) or ''

    for how in ('exact', 'part'):
        rows = rows_for(cd, key, how, path)
        acts = {}
        editorial = []
        for r in rows:
            win = [d for d in r['dates'] if s0 < d <= s1]
            if not win:
                continue
            if r['editorial']:
                editorial.append((r, win))
            for a in r['acts']:
                acts.setdefault(a, set()).update(win)
        tag = ' (for the Part or Division)' if how == 'part' else ''
        if len(acts) == 1:
            a, ds = next(iter(acts.items()))
            lab = next(r['label'] for r in rows if a in r['acts'])
            return ('act', a, max(ds), f'history table row "{lab}" of the version of {s1} cites {a[0]}/{a[1]}{tag}',
                    sorted(ds))
        if len(acts) > 1:
            return ('candidates', tuple(sorted(acts)), s1, 'history table cites more than one Act in the period', [])
        if editorial:
            r, ds = editorial[0]
            return ('editorial', (), max(ds), f'history table row "{r["label"]}": {r["how"]}', [])
        # rows new in this version's table without a commencement in the period (retrospective or undated)
        old = {(tuple(r['acts']), r['how']) for r in rows_for(pd_, key, how, path)}
        new = [r for r in rows if (tuple(r['acts']), r['how']) not in old and r['acts']]
        nacts = {a for r in new for a in r['acts']}
        if len(nacts) == 1:
            a = next(iter(nacts))
            return ('act', a, s1, f'history table row "{new[0]["label"]}" of the version of {s1} cites {a[0]}/{a[1]} '
                    f'(new in this version; commencement {", ".join(sorted({d for r in new for d in r["dates"]})) or "not given"}){tag}',
                    sorted({d for r in new for d in r['dates']}))
        if len(nacts) > 1:
            return ('candidates', tuple(sorted(nacts)), s1, 'history table cites more than one new Act', [])
    # date only: one Act in the principal table took effect in the window
    pdates = principal_dates(cd)
    inwin = {a for a, ds in pdates.items() if any(s0 < d <= s1 for d in ds)}
    if len(inwin) == 1:
        a = next(iter(inwin))
        win = sorted(d for d in pdates[a] if s0 < d <= s1)
        return ('date', a, win[-1], f'only Act in the principal table that took effect between {s0} and {s1}', win)
    return ('unexplained', (), s1, '', [])


KIND_RANK = {'start': 0, 'act': 1, 'date': 2, 'candidates': 3, 'editorial': 4, 'unexplained': 5}


def plan(titles: list[dict]) -> list[dict]:
    """Every change, grouped into commits: one per amending Act per day (across titles), and one per title per day for
    each publisher kind (baseline, candidates, editorial, unexplained)."""
    groups: dict[tuple, dict] = {}

    def group(day, kind, x, tslug):
        gk = 'act' if kind == 'date' else kind
        k = (day, KIND_RANK[gk], x if gk == 'act' else (), '' if gk == 'act' else tslug)
        g = groups.get(k)
        if g is None:
            g = groups[k] = {'day': day, 'kind': gk, 'x': x, 'files': {}, 'items': [], 'titles': {}}
        return g

    for t in titles:
        v0 = t['versions'][0]
        g = group(v0['from'], 'start', (), t['slug'])
        g['titles'][t['slug']] = t
        for k in v0['doc']['order']:
            g['files'][f'{t["slug"]}/{file_name(k)}'] = v0['doc']['units'][k]
        g['items'].append((t, None, v0, sorted(v0['doc']['order'])))
        t['changes'] = []
        for prev, cur in zip(t['versions'], t['versions'][1:]):
            pu, cu = prev['doc']['units'], cur['doc']['units']
            keys = sorted(set(pu) | set(cu))
            changed = [k for k in keys if pu.get(k) != cu.get(k)]
            per = {}
            for k in changed:
                kind, x, day, ev, ds = explain(prev, cur, k)
                per.setdefault((kind, x, day), []).append((k, ev, ds))
            for (kind, x, day), items in per.items():
                g = group(day, kind, x, t['slug'])
                g['titles'][t['slug']] = t
                for k, ev, ds in items:
                    g['files'][f'{t["slug"]}/{file_name(k)}'] = cu.get(k)
                g['items'].append((t, prev, cur, [k for k, _e, _d in items], [e for _k, e, _d in items],
                                   sorted({d for _k, _e, dd in items for d in dd}), kind))
            t['changes'].append((prev['from'], cur['from'], len(changed)))
    out = [groups[k] for k in sorted(groups, key=lambda k: (k[0], k[1], k[2], k[3]))]
    return out


def file_name(key: str) -> str:
    return f'{key}.md'


def unit_label(key: str) -> str:
    if key == 'title':
        return 'title'
    if key == 'headings':
        return 'headings'
    m = re.match(r's-0*(\d+.*)$', key)
    if m:
        return f's {m.group(1)}'
    m = re.match(r'(sch|app)(?:-(.+?))?/(?:cl-0*(.+)|schedule|(x-.+))$', key)
    if m:
        base = ('Sch' if m.group(1) == 'sch' else 'App') + (f' {m.group(2)}' if m.group(2) else '')
        if m.group(3):
            return f'{base} cl {m.group(3)}'
        if m.group(4):
            return f'{base} {m.group(4)}'
        return base
    return key


# ---------------------------------------------------------------- messages

def act_name(a: tuple, names: dict) -> str:
    n = names.get(a)
    return n['title'] if n else f'Act No. {a[0]} of {a[1]}'


def act_author(a: tuple, names: dict) -> tuple[str, str]:
    return f'{act_name(a, names)} (No. {a[0]} of {a[1]})', f'act-{a[1]}-{a[0]}@legislation-sa.invalid'


def wrap(text: str) -> list[str]:
    return textwrap.wrap(text, 100, break_on_hyphens=False, break_long_words=False)


def short_title(t: dict) -> str:
    return t['act']['title']


def message(g: dict, names: dict) -> tuple[tuple[str, str], str, list[str]]:
    kind, x, day = g['kind'], g['x'], g['day']
    titles = sorted(g['titles'])
    trailers = []
    listing = []
    for item in sorted(g['items'], key=lambda i: i[0]['slug']):
        t, keys = item[0], item[3]
        cur = item[2]
        labels = ', '.join(unit_label(k) for k in keys)
        listing += textwrap.wrap(f'{short_title(t)}: {labels}', 100, subsequent_indent='  ', break_on_hyphens=False,
                                 break_long_words=False)
    affecting = sorted(g['files'])
    if kind == 'start':
        t = g['titles'][titles[0]]
        v = t['versions'][0]
        princ = [r for r in v['doc']['principal']]
        author = PUBLISHER
        subj = f'{short_title(t)}: the Act as in force on {day}'
        body = wrap(f'The {short_title(t)} as in force on {day}: the first version the South Australian Legislation '
                    f'website publishes in RTF ({v["sub"].strip("()")}). Amendments in force before {day} are in this '
                    'text and not separated out; the legislative history at the end of the version names them.')
        if len(princ) == 1 and day in princ[0]['dates']:
            n = princ[0]
            author = (f'{n["title"]} (No. {n["no"]} of {n["year"]})', f'act-{n["year"]}-{n["no"]}@legislation-sa.invalid')
            subj = f'{short_title(t)}: the Act as it took effect'
            body = wrap(f'The {short_title(t)} as it took effect on {day}: the first version the South Australian '
                        f'Legislation website publishes, and no amending Act is in its history.')
        if t['pdf_only']:
            body += [''] + wrap(f'Earlier versions ({len(t["pdf_only"])}) are published only as PDF and are not in '
                                'this record.')
        trailers += ['Kind: baseline' if author == PUBLISHER else 'Kind: Act', f'In-force: {day}',
                     f'Source: {v["rtf"]}']
    elif kind in ('act', 'date'):
        author = act_author(x, names)
        tail = f'No. {x[0]} of {x[1]}'
        if len(titles) == 1:
            labels = ', '.join(unit_label(k) for i in g['items'] for k in i[3])
            subj = f'{short_title(g["titles"][titles[0]])}: {labels} ({tail})'
            if len(subj) > 72:
                n = sum(len(i[3]) for i in g['items'])
                subj = f'{short_title(g["titles"][titles[0]])}: {n} provisions ({tail})'
        else:
            subj = f'{act_name(x, names)} ({tail}): {len(titles)} Acts'
        body = [author[0] + '.', ''] + (['Changes:'] + listing)
        why = []
        for item in g['items']:
            t, prev, cur = item[0], item[1], item[2]
            if item[6] == 'act':
                w = (f'{short_title(t)}: the legislative history of the version in force from {cur["from"]} cites this '
                     f'Act for {"this provision" if len(item[3]) == 1 else "these provisions"}, with commencement '
                     f'{", ".join(item[5]) or "not given"}.')
            else:
                w = (f'{short_title(t)}: the history table names no Act for '
                     f'{"this provision" if len(item[3]) == 1 else "these provisions"}; this Act is the only one in '
                     f'the principal table that took effect between {prev["from"]} and {cur["from"]} '
                     f'(on {", ".join(item[5])}). Attributed by date only.')
            if day != cur['from']:
                w += f' No version was published for {day}; the text is that of the version of {cur["from"]}.'
            why += wrap(w)
        body += [''] + why
        n = names.get(x) or {}
        trailers += ['Kind: Act' if all(i[6] == 'act' for i in g['items']) else
                     'Kind: Act (by date only)' if all(i[6] == 'date' for i in g['items']) else
                     'Kind: Act (partly by date only)']
        if n.get('assent'):
            trailers.append(f'Assented: {n["assent"]}')
        trailers.append(f'In-force: {day}')
        if n.get('assent') and day < n['assent']:
            trailers.append('Date-note: in force before assent (retrospective)')
        for item in sorted(g['items'], key=lambda i: i[0]['slug']):
            if item[6] == 'act':
                rows = sorted({re.search(r'row "([^"]*)"', e).group(1) for e in item[4] if 'row "' in e})
                trailers.append(f'Evidence: {short_title(item[0])}: legislative history of the version of '
                                f'{item[2]["from"]} cites No. {x[0]} of {x[1]} for: ' + '; '.join(rows))
            else:
                trailers.append(f'Evidence: {short_title(item[0])}: date only; the only Act in the principal table '
                                f'that took effect between {item[1]["from"]} and {item[2]["from"]}')
        for item in g['items']:
            trailers.append(f'Version: {item[2]["rtf"]}')
    else:
        t = g['titles'][titles[0]]
        item = g['items'][0]
        prev, cur = item[1], item[2]
        author = PUBLISHER
        if kind == 'candidates':
            subj = f'{short_title(t)}: changes by two or more Acts (not credited)'
            body = wrap(f'The legislative history of the version in force from {cur["from"]} cites more than one Act '
                        f'that is new since the version of {prev["from"]} for these provisions, and no version '
                        'separates them. The change is credited to none of them.')
            trailers += ['Kind: two or more Acts (not credited)', f'In-force: {day}']
            for a in x:
                trailers.append(f'Candidate: {act_name(a, names)} (No. {a[0]} of {a[1]})')
        elif kind == 'editorial':
            subj = f'{short_title(t)}: editorial change'
            body = wrap(f'An editorial change under the Legislation Revision and Publication Act 2002, recorded in the '
                        f'legislative history of the version in force from {cur["from"]}.')
            trailers += ['Kind: editorial', f'In-force: {day}']
            for e in sorted(set(item[4])):
                trailers.append(f'Evidence: {e}')
        else:
            subj = f'{short_title(t)}: change no Act explains'
            body = wrap(f'The text changed between the version in force from {prev["from"]} and the version in force '
                        f'from {cur["from"]}, and the legislative history of the later version names no new Act for '
                        'these provisions, nor did exactly one Act take effect between the two days. Not credited '
                        'to any Act.')
            trailers += ['Kind: no Act named', f'In-force: {day}']
        body = ['Changes:'] + listing + [''] + body
        trailers.append(f'Version: {cur["rtf"]}')
    if len(subj) > 72:
        subj = subj[:69].rstrip() + '...'
    if day < '1970-01-01':
        body += ['', 'Git cannot record a date before 1970, so this commit is dated 1 January 1970; the day it took',
                 'effect is in the In-force trailer.']
    trailers += [f'Affecting: {p}' for p in affecting]
    msg = subj + '\n\n' + '\n'.join(body).strip('\n') + '\n\n' + '\n'.join(trailers) + '\n'
    return author, msg, affecting


# ---------------------------------------------------------------- the repository

def git(repo: Path, *args: str, env: dict | None = None, input: bytes | None = None) -> str:
    out = subprocess.run(['git', *args], cwd=repo, capture_output=True, env=env, input=input)
    if out.returncode:
        raise SystemExit(f'git {" ".join(args)} failed: {out.stderr.decode("utf-8", "replace")}')
    return out.stdout.decode('utf-8').replace('\r\n', '\n')


def write_repo(repo: Path, titles: list[dict], groups: list[dict], names: dict, today: str, docs,
               limit: int = 0, scope: dict | None = None) -> dict:
    remote = None
    if (repo / '.git').exists():
        r = subprocess.run(['git', 'remote', 'get-url', 'origin'], cwd=repo, capture_output=True)
        remote = r.stdout.decode().strip() or None
        for p in sorted(repo.iterdir()):
            if p.name == '.git':
                continue
            if p.is_dir():
                import shutil
                shutil.rmtree(p)
            else:
                p.unlink()
        import shutil
        shutil.rmtree(repo / '.git', onerror=lambda f, p, e: (os.chmod(p, 0o700), f(p)))
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, 'init', '-q', '-b', 'main')
    git(repo, 'config', 'core.autocrlf', 'false')
    if remote:
        git(repo, 'remote', 'add', 'origin', remote)
    seq = [0]
    out = io.BytesIO()
    mark = 0
    stats = {'commits': 0, 'kinds': {}, 'future': 0, 'limit': limit, 'scope': scope}
    tag_marks = {}
    ver_mark = {}

    def data(b: bytes):
        out.write(b'data %d\n' % len(b))
        out.write(b)
        out.write(b'\n')
    branch = 'main'
    last_main = None
    for g in groups:
        if g['day'] > today:
            if branch == 'main':
                branch = 'future'
                out.write(b'reset refs/heads/future\n')
                if last_main:
                    out.write(b'from :%d\n' % last_main)
                out.write(b'\n')
            stats['future'] += 1
        author, msg, files = message(g, names)
        when = commit_date(g['day'], seq)
        mark += 1
        out.write(f'commit refs/heads/{branch}\nmark :{mark}\n'.encode())
        out.write(f'author {author[0]} <{author[1]}> {when}\n'.encode('utf-8'))
        out.write(f'committer {COMMITTER[0]} <{COMMITTER[1]}> {when}\n'.encode('utf-8'))
        data(msg.encode('utf-8'))
        for p in files:
            txt = g['files'][p]
            if txt is None:
                out.write(b'D ' + p.encode('utf-8') + b'\n')
            else:
                out.write(b'M 100644 inline ' + p.encode('utf-8') + b'\n')
                data(txt.encode('utf-8'))
        out.write(b'\n')
        if branch == 'main':
            last_main = mark
            stats['commits'] += 1
            stats['kinds'][g['kind']] = stats['kinds'].get(g['kind'], 0) + 1
        if branch == 'main':
            ver_mark[g['day']] = mark   # the last main commit of each day (groups are in day order)
    # a version tag points at the last commit in force on or before the version's first day: the repository as it
    # stood that day, which is what tools/in_force_on.py reads for the day
    days = sorted(ver_mark)
    import bisect
    for t in titles:
        for v in t['versions']:
            if v['from'] > today:
                continue
            i = bisect.bisect_right(days, v['from'])
            if i:
                tag_marks[f'{t["slug"]}/{v["from"]}'] = ver_mark[days[i - 1]]
    for name, m in sorted(tag_marks.items()):
        out.write(f'reset refs/tags/{name}\nfrom :{m}\n\n'.encode('utf-8'))
    out.write(b'done\n')
    marks_file = repo / '.git' / 'lexifina-marks'
    r = subprocess.run(['git', 'fast-import', '--quiet', '--done', f'--export-marks={marks_file}'], cwd=repo,
                       input=out.getvalue(), capture_output=True)
    if r.returncode:
        raise SystemExit(f'git fast-import failed: {r.stderr.decode(errors="replace")}')
    marks_file.unlink()
    git(repo, 'reset', '-q', '--hard', 'main')
    stats['tags'] = len(tag_marks)
    if docs:
        docs(repo, titles, groups, names, today, stats)
        git(repo, 'add', '-A')
        when = commit_date(today, seq)
        env = {**os.environ, 'GIT_AUTHOR_NAME': COMMITTER[0], 'GIT_AUTHOR_EMAIL': COMMITTER[1],
               'GIT_AUTHOR_DATE': when, 'GIT_COMMITTER_NAME': COMMITTER[0], 'GIT_COMMITTER_EMAIL': COMMITTER[1],
               'GIT_COMMITTER_DATE': when}
        git(repo, 'commit', '-q', '--cleanup=verbatim', '-F', '-', env=env,
            input=(f'Documentation, tables, tools, corpus.json and REPRODUCE.md\n\nNo law text changes.\n\n'
                   f'In-force: {today}\nKind: documentation\n').encode('utf-8'))
    return stats


BUILT = sacache.CACHE / 'built-acts.json'   # the A-Z paths of the Acts in the last build (written by main)


def select_acts(limit: int = 0, acts_file: str | None = None) -> list[dict]:
    """The current Acts of the A-Z list (the first `limit`), or exactly those listed in `acts_file`: the fetch may
    complete more Acts at any time, so a rebuild or a check of a build names the Acts it means."""
    acts = json.loads((sacache.CACHE / 'acts.json').read_text('utf-8'))
    acts = [a for a in acts if 'Ceased' not in a['note']]
    if acts_file:
        pin = json.loads(Path(acts_file).read_text('utf-8'))
        keep = set(pin['acts'] if isinstance(pin, dict) else pin)
        return [a for a in acts if a['path'] in keep]
    return acts[:limit] if limit else acts


def scope_counts(limit: int) -> dict:
    """How many of the selected Acts have text, none on the site, or a page not (yet) read: for the README."""
    selected = select_acts(limit)
    pages = [act_versions(a) for a in selected]
    return {'selected': len(selected), 'all': len(select_acts()), 'limited': bool(limit),
            'no_text': sum(1 for t in pages if t is not None and not t['versions']),
            'unread': sum(1 for t in pages if t is None)}


def build(today: str, workers: int, limit: int = 0, acts_file: str | None = None):
    titles = load_acts(select_acts(limit, acts_file), workers)
    names = names_of(titles)
    groups = plan(titles)
    return titles, names, groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'australia-south-australia-law-build'))
    ap.add_argument('--today', required=True)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--no-docs', action='store_true')
    ap.add_argument('--verify-rebuild', action='store_true')
    ap.add_argument('--acts-file', default=None, help='build exactly the Acts listed (JSON list of A-Z paths)')
    a = ap.parse_args()
    if a.verify_rebuild:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, '-X', 'utf8', str(HERE / 'build.py'), '--repo', tmp, '--today',
                                a.today, '--workers', str(a.workers), '--acts-file', a.acts_file or str(BUILT)]
                               + (['--limit', str(a.limit)] if a.limit else []),
                               capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(r.stderr)

            def hashes(p):
                log = git(Path(p), 'log', '--format=%H', 'main').split()
                tags = git(Path(p), 'show-ref', '--tags')
                fut = subprocess.run(['git', 'rev-parse', '--verify', '-q', 'future'], cwd=p, capture_output=True).stdout
                return log, hashlib.sha1(tags.encode()).hexdigest(), fut
            x, y = hashes(a.repo), hashes(tmp)
            rec = {'identical': x == y, 'main': x[0][0], 'rebuilt_main': y[0][0], 'commits': len(x[0]),
                   'history_identical': x[0][1:] == y[0][1:], 'tags_identical': x[1] == y[1]}
            print(json.dumps(rec, indent=1))
            p = HERE / 'verification.json'
            ver = json.loads(p.read_text('utf-8')) if p.exists() else {}
            ver['rebuild'] = rec
            p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', 'utf-8', newline='\n')
            for f in Path(tmp).rglob('*'):
                try:
                    os.chmod(f, 0o700)
                except OSError:
                    pass
        raise SystemExit(0 if rec['identical'] else 1)
    titles, names, groups = build(a.today, a.workers, a.limit, a.acts_file)
    # the pin: exactly these Acts and the scope counts of this build, so a rebuild or a check can name them
    if a.acts_file:
        pin = json.loads(Path(a.acts_file).read_text('utf-8'))
        scope = pin.get('scope') if isinstance(pin, dict) else None
    else:
        scope = scope_counts(a.limit)
        BUILT.write_text(json.dumps({'acts': sorted(t['act']['path'] for t in titles), 'scope': scope}, indent=0)
                         + '\n', 'utf-8')
    docs = None
    if not a.no_docs:
        from reproduce import write_docs
        docs = write_docs
    stats = write_repo(Path(a.repo), titles, groups, names, a.today, docs, a.limit, scope)
    nv = sum(len(t['versions']) for t in titles)
    print(json.dumps({'titles': len(titles), 'versions': nv, **stats}, sort_keys=True))


if __name__ == '__main__':
    main()
