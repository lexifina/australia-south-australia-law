"""Writes the documentation of the repository: README.md, LICENSE, SOURCES.md, corpus.json, REPRODUCE.md, changes.csv,
and copies the tools. Called by build.py; every number is filled in at build time."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import platform
import subprocess
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Every file written here goes through publish_gate (gitlaw_common, private), which refuses anything the private rule
# of 2026-10-29 keeps out of a published record and scrubs this machine's home path.
sys.path.append(str(HERE.parent / 'gitlaw_common'))   # last, so this corpus's own build.py is the one imported
try:
    from publish_gate import publish_gate
except ImportError:          # an outside reader rebuilding from the published tools: no private rule to apply
    if os.environ.get('GITLAW_PUBLIC_REBUILD') != '1':
        raise SystemExit('publish gate missing: run from lexifina/scripts, or set GITLAW_PUBLIC_REBUILD=1')

    def publish_gate(files):
        return files
SITE = 'https://www.legislation.sa.gov.au'
TOOLS = ('build.py', 'sacache.py', 'sartf.py', 'reproduce.py', 'check_repo.py', 'check_words.py', 'test_repo.py',
         'in_force_on.py')

ATTRIBUTION = ('Based on content from the South Australian Legislation website at {day}. For the latest information on '
               'South Australian Government legislation, please go to www.legislation.sa.gov.au/ .')
DISCLAIMER = ('Despite any headers, footers or other markings, this legislation is NOT published under the Legislation '
              'Revision and Publication Act 2002. To access legislation that is published under the Legislation '
              'Revision and Publication Act 2002, please go to www.legislation.sa.gov.au/ .')


def long_day(day: str) -> str:
    import datetime as dt
    d = dt.date.fromisoformat(day)
    return f'{d.day} {d.strftime("%B")} {d.year}'


def write(repo: Path, name: str, text: str):
    p = repo / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8', newline='\n')


def retrieval_days(titles: list[dict]) -> tuple[str, str]:
    """The first and last day on which a file in this build was retrieved (from its metadata), so the documents
    depend only on the files used, not on what was retrieved since."""
    import sacache
    import build
    import json as _json
    days = []
    for t in titles:
        urls = [sacache.act_page_url(t['act']['path'])] + [v['rtf'] for v in t['versions']]
        for u in urls:
            m = sacache.cache_path(u).with_suffix('.json')
            if m.exists():
                days.append(_json.loads(m.read_text('utf-8'))['fetched'][:10])
    return (min(days), max(days)) if days else ('', '')


def write_docs(repo: Path, titles: list[dict], groups: list[dict], names: dict, today: str, stats: dict):
    import build
    files: dict[str, bytes] = {}

    def put(name: str, text: str):
        files[name] = text.encode('utf-8')
    first_got, got = retrieval_days(titles)
    got = got or today
    first = min(g['day'] for g in groups)
    last = max(g['day'] for g in groups if g['day'] <= today)
    nver = sum(len(t['versions']) for t in titles)
    npdf = sum(len(t['pdf_only']) for t in titles)
    kinds = stats['kinds']
    lim = f' --limit {stats["limit"]}' if stats.get('limit') else ''
    sc = stats.get('scope') or build.scope_counts(stats.get('limit') or 0)
    n_empty, n_nopage = sc['no_text'], sc['unread']
    nmiss = sum(len(t.get('missing', [])) for t in titles)
    scope = (f"the first {sc['selected']} of the {sc['all']} Acts in force on the site's A-Z list"
             if sc['limited'] else f"all {sc['selected']} Acts in force on the site's A-Z list")
    partial = (f"\n**This record is partial.** It covers the first {sc['selected']:,} of the {sc['all']:,} current Acts "
               f"({len(titles):,} with text). Later rebuilds will add the other Acts and re-date the history, so commit "
               "hashes and tags will change.\n" if sc['limited'] else '')
    ex = next((t for t in titles if t['slug'] == 'constitution-act-1934'), titles[0])
    exs = ex['slug']
    readme = f"""# South Australia law, change by change

The Acts of South Australia as a git repository: {len(titles)} Acts in force, each with every consolidated version the
[South Australian Legislation website](https://www.legislation.sa.gov.au) publishes as RTF ({nver} versions). Each
amending Act is one commit, credited to that Act and dated the day it took effect, at noon in Adelaide.

This build covers {scope}: {len(titles)} have consolidated text; {n_empty} have none on the site (Acts of limited
application, repealing Acts and old titles, whose pages list no version){f'; {n_nopage} could not be read' if n_nopage else ''}.
{partial}
## The questions, as git commands

| Question | Command |
| --- | --- |
| What did a section say on a day? | `python tools/in_force_on.py 2015-06-01 {exs} s-028` |
| The whole Act on a day | `python tools/in_force_on.py 2015-06-01 {exs}` |
| Which Act wrote each line of a section? | `git blame {exs}/s-028.md` |
| Every change to a section, with the Act and the day | `git log --follow {exs}/s-028.md` |
| Everything one Act changed | `git log --author="(No. 40 of 2021)" --stat` |
| A version of an Act, as published | `git show {exs}/2021-11-04:{exs}/s-006.md` (tags `<act>/<first day>`) |
| What changed between two versions | `git diff --stat {exs}/2017-12-12 {exs}/2021-01-01 -- {exs}` |
| The commit that holds a day's text, and why | `python tools/in_force_on.py 2015-06-01 {exs} --commit` |
| Every change with its Act and day | [`changes.csv`](changes.csv) |
| Changes two Acts could have made (credited to neither) | `git log --grep="Kind: two or more Acts"` |
| Changes credited by date only | `git log --grep="by date only"` |
| Changes no Act explains | `git log --grep="Kind: no Act named"` |

## The dates, and which one we use

A South Australian Act has the day it was assented to and the day (or days) its provisions came into operation.
**Commits are dated the day the change took effect**, at noon in Adelaide: ACST (UTC+9:30), or ACDT (UTC+10:30) in
daylight saving time (the rules are written out in `tools/build.py`). Every commit carries `In-force: YYYY-MM-DD`; the
assent day is in `Assented:`. The site gives each version the period it was in force ("12 May 2023 - 17 April 2024");
the legislative history at the end of each version gives each amending Act's commencement. A change is dated by the
commencement the history gives for that provision; where that day falls between two versions, the text is that of the
later version. To ask what the law was on a day with plain git, use the end of that day:
`git rev-list -1 --before="2015-06-01T23:59:59+09:30" main` (a bare date is read at the time of day you run it).
Git cannot record a date before 1970: such commits are dated 1 January 1970, one second apart, with the real day in
`In-force:`. Some Acts took effect before they were assented to (a retrospective commencement); `Date-note:` says so.

## What "author" means

The author of a commit is the Act that made the change, with an address ending `@legislation-sa.invalid` that cannot
exist (`Constitution (Independent Speaker) Amendment Act 2021 (No. 40 of 2021)`), so `git blame` names the Act. Each
version's legislative history has a table, "Provisions amended", with a row per provision and Act ("s 6(1) amended by
40/2021 s 3(1) ... 4.11.2021"). Between two versions, a changed provision is credited to the Act that is new in that
provision's rows. Rules, each labelled in the commit:

- Two or more new Acts for one provision, with no version between them: credited to none, committed by the publisher
  with `Candidate:` trailers.
- No row names an Act, but exactly one Act in the table of amending Acts took effect between the two versions: credited
  to that Act, `Kind: Act (by date only)` with an `Evidence:` trailer.
- Editorial changes made under the Legislation Revision and Publication Act 2002 and changes nothing explains are
  committed by `South Australian Legislation (Commissioner for Legislation Revision and Publication)` and labelled.
- Each Act's first commit is its earliest RTF version; amendments before it are in its text and not separated out.
- Blame credits whole lines; use `git log -p --word-diff` for the words.

Counts in this build: {stats['commits']} law commits ({', '.join(f'{k}: {v}' for k, v in sorted(kinds.items()))}),
{stats['tags']} version tags.

## How it is checked

`tools/check_repo.py` reads every version of every Act back from git (its tag) and compares it with the official RTF,
file by file; `tools/check_words.py` compares the letters and digits of every version with the raw RTF read by a
separate minimal extractor; `tools/test_repo.py` checks that the commands above work. Results are in
[`REPRODUCE.md`](REPRODUCE.md).

## Files

Each Act is a folder named as on the site (`{exs}/`). Each section is one file: `s-028.md` is section 28, `s-028A.md`
section 28A. A schedule is a folder: `sch-1/schedule.md` holds its heading and any text before its clauses,
`sch-1/cl-003.md` its clause 3. `title.md` is the title and long title; `headings.md` lists the chapter, part and
division headings in order. In each file the first line is the section's number and heading, then one line per
paragraph (subsection, paragraph, definition, note, penalty). Table cells are separated by `\u2502`. A repealed section
is removed.

## What it does not show

- Versions published only as PDF ({npdf} older versions of these Acts, mostly before 2003), Acts that have ceased,
  Acts with no consolidated version on the site, and regulations.{f' {nmiss} RTF versions that could not be retrieved are left out; the changes they hold fall into the next version.' if nmiss else ''}
- The text on a day between two versions other than as rebuilt from the Acts' commencements (above).
- Which of two Acts of one period made a change, where both are new in the history (above).
- The legislative history tables themselves, the tables of contents and footnote markers.

The authorised text is the one on the South Australian Legislation website.

## How it is built

`tools/build.py` writes this repository from scratch from the files retrieved from the site (listed with their
SHA-256 in [`DATA-SHA256SUMS`](DATA-SHA256SUMS)); the same files give the same hashes. See [`REPRODUCE.md`](REPRODUCE.md)
and [`corpus.json`](corpus.json). After a rebuild, `git fetch --force --tags` replaces stale tags in an existing clone.

## Licence

{ATTRIBUTION.format(day=long_day(got))}

{DISCLAIMER}

The content is licensed by the State of South Australia under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/);
it has been changed (split into files by provision, white space normalised, history reconstructed). See
[`LICENSE`](LICENSE) and [`SOURCES.md`](SOURCES.md). **This repository is private.**
"""
    put('README.md', readme)
    put('LICENSE', f"""The legal text in this repository

{ATTRIBUTION.format(day=long_day(got))}

{DISCLAIMER}

Copyright in the content of www.legislation.sa.gov.au is owned by the State of South Australia, which licenses it under
the Creative Commons Attribution 4.0 International Licence (CC BY 4.0), https://creativecommons.org/licenses/by/4.0/
(Copyright Notice, https://www.legislation.sa.gov.au/copyright, paragraphs 3, 4, 7 and 9). The State's emblems
(Piping Shrike, Royal Arms) are not reproduced. Changes made: each consolidated version (RTF) of each Act has been split
into one file per provision, with white space, soft hyphens and footnote markers normalised, and the history between
versions reconstructed into commits from the versions' legislative history tables. Neither the State of South
Australia nor the Commissioner for Legislation Revision and Publication endorses this repository.

The code, documentation and arrangement

Copyright Lexifina. The tools in tools/ and the documentation may be used under CC BY 4.0 as well.
""")
    put('SOURCES.md', f"""# Sources

- South Australian Legislation, {SITE}: Acts A-Z (`/legislation/acts/consolidated`), each Act's page (`/lz?path=/c/a/<title>`)
  with its current and historical versions and their in-force periods, and each version's RTF
  (`/_legislation-documents/lz/c/a/<act>/<version>/<file>.un.rtf`).
- Legal basis for reuse: the site's Copyright Notice ({SITE}/copyright): content licensed under CC BY 4.0, with the
  attribution statement and disclaimer reproduced in LICENSE. The site's Disclaimer ({SITE}/disclaimer) explains that
  only versions published under the Legislation Revision and Publication Act 2002 are authorised; this copy is not.
- Data retrieved from {first_got} to {got}. The SHA-256 of every RTF the build read is in
  [`DATA-SHA256SUMS`](DATA-SHA256SUMS).
""")
    # changes.csv
    rows = []
    for g in groups:
        if g['day'] > today:
            continue
        for item in g['items']:
            t = item[0]
            who = (f'{build.act_name(g["x"], names)} (No. {g["x"][0]} of {g["x"][1]})' if g['kind'] == 'act' else '')
            rows.append({'in_force': g['day'], 'act': t['act']['title'], 'kind': item[6] if len(item) > 6 else g['kind'],
                         'by': who, 'provisions': ' '.join(build.unit_label(k) for k in item[3]),
                         'version_from': item[2]['from']})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=['in_force', 'act', 'kind', 'by', 'provisions', 'version_from'], lineterminator='\n')
    w.writeheader()
    w.writerows(rows)
    put('changes.csv', buf.getvalue())
    corpus = {
        'corpus': 'au/south-australia/acts',
        'parent': 'au/federal/acts',
        'name': 'South Australian law: the Acts in force, every consolidated version in RTF',
        'label': 'South Australia',
        'language': 'en',
        'acts': len(titles),
        'history_from': first,
        'record_to': last,
        'record_note': (f'{len(titles)} South Australian Acts in force, each from its earliest RTF version on the South '
                        f'Australian Legislation website through every later version ({nver} versions) to {last}; '
                        f'data retrieved {got}. Commits between versions are dated by the commencement in the '
                        'legislative history; only version days are checked against official text. Older versions '
                        f'published only as PDF ({npdf}) and ceased Acts are not included.'),
        'in_force_trailer': 'In-force',
        'built': today,
        'checked_on': today,
        'history': 'in-force',
        'dates': {'commit': 'in-force', 'other_trailers': ['Assented'],
                  'time': '12:00 Adelaide (ACST, UTC+9:30; ACDT, UTC+10:30, in daylight saving time)'},
        'scope': {
            'trailer': 'Applies',
            'when_absent': ('this corpus records no scope notes (no commit carries Applies:); the absence is not a '
                            'statement that the change reaches every case'),
            'default_reach': {
                'rule': 'presumption against retrospective operation; repeal or amendment does not affect what was done or accrued before',
                'meaning': ('an Act is presumed not to operate retrospectively on substantive rights unless it says so; '
                            'the repeal or amendment of an Act does not affect its previous operation or any right, '
                            'liability or penalty acquired or incurred under it, unless the contrary intention appears'),
                'authority': ('Acts Interpretation Act 1915 (SA) s 16 (effect of repeal or amendment); Maxwell v Murphy '
                              '(1957) 96 CLR 261 (common-law presumption); not checked in this corpus'),
                'decided_here': False}},
        'tool': 'tools/in_force_on.py',
        'citation': {'file': r'([a-z0-9-]+)/s-0*([0-9]+[A-Z]*)\.md', 'cites': r'(section|s\.?) ?{n}([^0-9A-Za-z]|$)'},
        'verification': ('Every RTF version of every Act is checked against its official RTF on its version tag '
                         '(file by file, and letters and digits against the raw RTF).'),
    }
    put('corpus.json', json.dumps(corpus, indent=2, ensure_ascii=False) + '\n')
    for n in TOOLS:
        if (HERE / n).exists():
            files['tools/' + n] = (HERE / n).read_bytes()
    ver = {}
    vp = HERE / 'verification.json'
    if vp.exists():
        ver = json.loads(vp.read_text('utf-8'))
    git_v = subprocess.run(['git', '--version'], capture_output=True).stdout.decode().strip()
    sums = []
    for t in titles:
        for v in t['versions']:
            import sacache
            b = sacache.cached(v['rtf']) or b''
            sums.append(f'{hashlib.sha256(b).hexdigest()}  {v["rtf"]}')
    put('DATA-SHA256SUMS', '\n'.join(sorted(sums)) + '\n')
    put('REPRODUCE.md', f"""# Reproducing this repository

Everything here can be rebuilt from public data with the tools in `tools/`.

## Steps

1. Put the retrieved files from www.legislation.sa.gov.au (the Acts A-Z lists, each Act's page and the RTF of every
   version) where `tools/sacache.py` reads them, each RTF matching its SHA-256 in `DATA-SHA256SUMS`. How the files
   were retrieved is not part of this record.
2. `python -X utf8 tools/build.py --today {today}{lim}` writes the repository from the cache. `--today` is required: it
   dates the documentation commit and decides which versions are in force (later ones would go on a `future` branch).
3. `python -X utf8 tools/check_repo.py --today {today}{lim}` and `python -X utf8 tools/check_words.py --today {today}{lim}`
   compare every version with its RTF; `python -X utf8 tools/test_repo.py` runs the README's commands;
   `python -X utf8 tools/build.py --today {today}{lim} --verify-rebuild` rebuilds into a temporary folder and compares hashes.

The RTF files the build read are listed with their SHA-256 in [`DATA-SHA256SUMS`](DATA-SHA256SUMS); the site replaces
the `current` file of an Act when a new version is published, so files retrieved later can differ.

## This build

- Build day: {today}; data retrieved {first_got} to {got}.
- {len(titles)} Acts, {nver} versions, {stats['commits']} law commits + 1 documentation commit, {stats['tags']} tags,
  {stats.get('future', 0)} commits on `future`.
- Software: Python {platform.python_version()}, {git_v}; no third-party packages (the RTF reader is `tools/sartf.py`).
- Verification: {json.dumps({k: v for k, v in ver.items() if k != 'rebuild'}, sort_keys=True) if ver else 'see tools/check_repo.py (run after the build)'}

## Decisions made by hand

- Section files are found by the RTF paragraph styles the site's publishing system writes (`clausehead...`,
  `parthead...`, `schedulehead`); schedule headings without a style are found by their text ("Schedule 1\u2014").
- The body starts after the table of contents (whose last entry links to the legislative history).
- Attribution rules are in README.md. History rows listed under a section (definitions, "cl 3" under a schedule) are
  read as rows of that section or schedule.
- Footnote markers (superscript numbers) are left out.

## AI disclosure

The RTF reader, builder and checks were written by an AI coding agent (Claude, model claude-opus-5-5) for
Lexifina. No model reads or writes legal text at build time: every word comes from the RTF files.
""")
    for name, data in publish_gate(files).items():
        out = repo / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)

