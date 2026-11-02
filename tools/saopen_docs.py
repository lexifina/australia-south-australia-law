"""Updates the documentation for Acts appended from the open data (called by saappend.py): README.md, SOURCES.md,
LICENSE, corpus.json, changes.csv, DATA-SHA256SUMS, REPRODUCE.md and tools/. Each run rewrites its own sections from
the repository as it stands, so the documents always describe main."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.append(str(HERE.parent / 'gitlaw_common'))
try:
    from publish_gate import publish_gate
except ImportError:
    if os.environ.get('GITLAW_PUBLIC_REBUILD') != '1':
        raise SystemExit('publish gate missing: run from lexifina/scripts, or set GITLAW_PUBLIC_REBUILD=1')

    def publish_gate(files):
        return files

import build as B  # noqa: E402
import saopen  # noqa: E402

FIRST = 267            # the Acts of the first build (from the website's RTF versions)
FIRST_VERSIONS = 1805
TOOLS = ['saxml.py', 'saopen.py', 'saappend.py', 'saopen_docs.py', 'check_open.py', 'in_force_on.py']
ATTRIBUTION = ('Office of Parliamentary Counsel, Government of South Australia, Legislative Database Update Package '
               '(XML) and Legislative Database, sourced on {day}, '
               'https://data.sa.gov.au/data/dataset/database-update-package-xml and '
               'https://data.sa.gov.au/data/dataset/legislative-database')


def section(text: str, heading: str, body: str, before: str) -> str:
    """Replace the section `heading` (to the next "## "), or insert it before the section `before`."""
    h = f'## {heading}\n'
    if h in text:
        i = text.index(h)
        j = text.find('\n## ', i + len(h))
        return text[:i] + h + '\n' + body.strip('\n') + '\n' + (text[j:] if j >= 0 else '')
    k = text.index(f'## {before}\n')
    return text[:k] + h + '\n' + body.strip('\n') + '\n\n' + text[k:]


def write_docs(repo: Path, titles: list[dict], groups: list[dict], names: dict, day: str, stats: dict):
    def read(n):
        p = repo / n
        return p.read_text('utf-8') if p.exists() else ''
    folders = sorted(l for l in B.git(repo, 'ls-tree', '-d', '--name-only', 'main').split('\n')
                     if l and l not in ('tools', 'data'))
    nacts = len(folders)
    added = nacts - FIRST
    tags = [t for t in B.git(repo, 'tag', '--list').split() if '/' in t]
    nver = len(tags)
    added_ver = nver - FIRST_VERSIONS
    files: dict[str, str | bytes] = {}

    # changes.csv: append this run's rows
    rows = []
    for g in groups:
        for item in g['items']:
            t = item[0]
            who = (f'{B.act_name(g["x"], names)} (No. {g["x"][0]} of {g["x"][1]})' if g['kind'] == 'act' else '')
            rows.append({'in_force': g['day'], 'act': t['act']['title'], 'kind': item[6] if len(item) > 6 else g['kind'],
                         'by': who, 'provisions': ' '.join(B.unit_label(k) for k in item[3]),
                         'version_from': item[2]['from']})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=['in_force', 'act', 'kind', 'by', 'provisions', 'version_from'], lineterminator='\n')
    w.writerows(rows)
    files['changes.csv'] = read('changes.csv') + buf.getvalue()

    # DATA-SHA256SUMS: the open-data files this run read
    sums = read('DATA-SHA256SUMS').splitlines()
    have = set(sums)
    for t in titles:
        for v in t['versions']:
            line = f'{v["sha256"]}  {v["rtf"]}'
            if line not in have:
                sums.append(line)
                have.add(line)
    files['DATA-SHA256SUMS'] = '\n'.join(sums) + '\n'

    readme = read('README.md')
    i = readme.index('## The questions')
    intro = f"""# South Australia law, change by change

The Acts of South Australia as a git repository: {nacts} Acts in force, {nver} versions. {FIRST} of them have every
consolidated version the [South Australian Legislation website](https://www.legislation.sa.gov.au) publishes as RTF
({FIRST_VERSIONS} versions); {added} more come from the South Australian Government's open data on
[data.sa.gov.au](https://data.sa.gov.au/data/dataset/database-update-package-xml), from 2017 on ({added_ver} versions;
see "Acts added from the open data"). Each amending Act is one commit, credited to that Act and dated the day it took
effect, at noon in Adelaide.

**This record is partial.** It holds {nacts} of the 1,390 Acts on the A-Z list of Acts in force (many of the rest are
Acts of limited application, repealing Acts and old titles with no consolidated text). More Acts are appended as they
are checked; nothing already published is rewritten.

"""
    readme = intro + readme[i:]
    first_in = min((v['from'] for t in titles for v in t['versions']), default='')
    body = f"""{added} Acts were added after the first {FIRST}, from the open data of the Office of Parliamentary Counsel on
data.sa.gov.au: the Legislative Database of 17 November 2017 (every Act then in operation, as RTF) and every
fortnightly Legislative Database Update Package (XML) since (each Act substituted or added in the fortnight, as a whole
version with the day it took effect). They are read and credited as the first {FIRST}: each version's legislative
history names the Act that changed each provision, with its commencement. For these Acts:

- the history starts with the version in force on 17 November 2017 (for an Act passed later, its first version in the
  packages); amendments before it are in that text and not separated out;
- a version replaced within a fortnight, before the next package, is not in the packages: its changes fall into the
  next version;
- the commits were appended to main after those of the first {FIRST} Acts, because main only grows: they come after
  commits with later dates. Each commit's day is in its `In-force:` trailer, and `tools/in_force_on.py` reads an Act's
  text on a day from the commits that touch that Act. `git rev-list --before` (committer dates) answers for the first
  {FIRST} Acts only; for the others use the tool, or `git log -- <act>/` and the trailers;
- the 2017 versions are RTF and the later ones XML. Where a provision differs between two versions only in white space,
  table bars or a non-breaking hyphen for a hyphen (the two formats print a few spaces differently), it keeps the earlier version's text, so a change of
  format is never recorded as a change of law;
- the first 25 of them were appended Act by Act; the rest as one block: the first version of every Act (A-Z), then
  every later change of all of them, in day order. The version tags `<act>/<first day>` point at the commit that
  holds that version.

`tools/saopen.py` reads the open data, `tools/saxml.py` the XML versions (written to print what `tools/sartf.py` prints
for the same version's RTF), `tools/saappend.py` appends, and `tools/check_open.py` checks every version of every Act
on main, file by file and, with a separate reader, letter by letter.
"""
    readme = section(readme, 'Acts added from the open data', body, 'The dates, and which one we use')
    if 'Acts appended from the open data: versions before' not in readme:
        readme = readme.replace('## What it does not show\n\n',
                                '## What it does not show\n\n- Acts appended from the open data: versions before the one '
                                'in force on 17 November 2017, and versions replaced within a fortnight.\n', 1)
    if 'tools/check_open.py' not in readme.split('## How it is checked')[1].split('\n## ')[0]:
        readme = readme.replace('Results are in\n[`REPRODUCE.md`](REPRODUCE.md).',
                                'Results are in\n[`REPRODUCE.md`](REPRODUCE.md). Since Acts were appended, '
                                '`tools/check_open.py` does the same for every Act on main, choosing each version\'s '
                                'commit by the Act\'s own commits and In-force trailers.', 1)
    lic = (f'\nThe Acts added from the open data: {ATTRIBUTION.format(day=day)}. Licensed under the Creative Commons '
           'Attribution 4.0 Licence (data.sa.gov.au/copyright); the dataset is published under CC BY 4.0. '
           'The South Australian Government does not endorse this repository.\n')
    lm = readme.index('## Licence')
    tail = readme[lm:]
    tail = re.sub(r'\nThe Acts added from the open data: .*?endorse this repository\.\n', '', tail, flags=re.S)
    tail = tail.replace('\nThe content is licensed by', lic + '\nThe content is licensed by', 1)
    files['README.md'] = readme[:lm] + tail

    src = read('SOURCES.md')
    src = re.sub(r'\n- Open data, data\.sa\.gov\.au.*', '', src, flags=re.S).rstrip('\n') + '\n'
    src += f"""- Open data, data.sa.gov.au (the South Australian Government's data portal, CKAN), for the Acts added from the open
  data: the dataset "Legislative Database Update Package (XML)" ({saopen.DATASET_XML}), every fortnightly package from
  17 November 2017, and from the dataset "Legislative Database" ({saopen.DATASET_BASE}) the snapshot of 17 November 2017
  (Acts, Policies and Proclamations; RTF). Publisher: Office of Parliamentary Counsel. Licence: Creative Commons
  Attribution (the datasets' licence, CC BY 4.0; data.sa.gov.au/copyright: "the content of this website is licensed
  under the Creative Commons Attribution 4.0 Licence"). Attribution as the portal asks: {ATTRIBUTION.format(day=day)}.
  The SHA-256 of every file read is in [`DATA-SHA256SUMS`](DATA-SHA256SUMS) (package URL, then `#` and the file in it).
"""
    files['SOURCES.md'] = src

    licence = read('LICENSE')
    licence = re.sub(r'\nThe Acts added from the open data.*?(?=\nThe code, documentation)', '', licence, flags=re.S)
    licence = licence.replace('\nThe code, documentation', f"""
The Acts added from the open data

{ATTRIBUTION.format(day=day)}. The datasets are licensed by the Government of South Australia under the Creative
Commons Attribution 4.0 Licence (https://creativecommons.org/licenses/by/4.0/; https://data.sa.gov.au/copyright).
Changes made: each version split into one file per provision, white space normalised, and the history between versions
reconstructed into commits from the versions' legislative history tables. The South Australian Government does not
endorse this repository.

The code, documentation""", 1)
    files['LICENSE'] = licence

    corpus = json.loads(read('corpus.json'))
    law = B.git(repo, 'log', '--format=%(trailers:key=In-force,valueonly,separator=)%x09%(trailers:key=Kind,valueonly,separator=)', 'main')
    days = sorted(d for d, k in (l.split('\t') for l in law.splitlines() if '\t' in l) if d and k != 'documentation')
    corpus.update({
        'name': 'South Australian law: the Acts in force, consolidated versions from the legislation website and the open data',
        'acts': nacts, 'history_from': days[0], 'record_to': days[-1], 'built': day, 'checked_on': day,
        'record_note': (f'{nacts} South Australian Acts in force: {FIRST} from their earliest RTF version on the South '
                        f'Australian Legislation website ({FIRST_VERSIONS} versions, data retrieved 2026-10-29), and '
                        f'{added} from the open data on data.sa.gov.au, from the version in force on 17 November 2017 '
                        f'(or the Act\'s first version since) through every fortnightly update package ({added_ver} '
                        f'versions, sourced {day}); to {days[-1]}. The Acts from the open data were appended after the '
                        'others: choose commits by their In-force trailer, per Act. Commits between versions are dated '
                        'by the commencement in the legislative history; only version days are checked against official '
                        'text. Older versions published only as PDF, versions before 17 November 2017 of the Acts from '
                        'the open data, and ceased Acts are not included.'),
        'verification': ('Every version of every Act is checked against its official source on the Act\'s own commits '
                         '(file by file, and letters and digits with a separate reader).'),
        'order': 'appended: an Act\'s commits are in date order, but Acts added later come after commits with later dates',
    })
    files['corpus.json'] = json.dumps(corpus, indent=2, ensure_ascii=False) + '\n'

    rep = read('REPRODUCE.md')
    log_line = (f'- {day}: {len(titles)} Acts ({titles[0]["slug"]} .. {titles[-1]["slug"]}), {stats["versions"]} versions, '
                f'{stats["commits"]} commits, {stats["tags"]} tags, appended to {stats["head"][:12]}.')
    if '## Acts appended from the open data' not in rep:
        rep = rep.rstrip('\n') + f"""

## Acts appended from the open data

The Acts after the first {FIRST} are appended to main, never rebuilt (`tools/saappend.py`):

1. Put the open-data files where `tools/saopen.py` reads them (SA_OPENDATA: `base/2017-11-17-a-p-pol.zip`, the
   update packages in `xml/` as `<publication day>_<resource id>.zip`, and `xml/resources.json`, the dataset's CKAN
   resource list), each matching its SHA-256 in `DATA-SHA256SUMS`; then `python -X utf8 tools/saopen.py index`.
2. `python -X utf8 tools/saappend.py plan --docs-date D`, then `python -X utf8 tools/saappend.py append --docs-date D
   --acts N` on a clone whose main is the published main: the next N Acts (A-Z) not on main yet. The same main and
   the same files give the same commits.
3. `python -X utf8 tools/check_open.py` checks every version of every Act on main.

Runs so far:
"""
    files['REPRODUCE.md'] = rep.rstrip('\n') + '\n' + log_line + '\n'

    for n in TOOLS:
        files['tools/' + n] = (HERE / n).read_bytes()
    files = {k: (v.encode('utf-8') if isinstance(v, str) else v.replace(b'\r\n', b'\n')) for k, v in files.items()}
    for name, data in publish_gate(files).items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
