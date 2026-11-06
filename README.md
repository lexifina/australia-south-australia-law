# South Australia law, change by change

Part of [lexifina/global-law](https://github.com/lexifina/global-law), one git history for every country's law: this is the law of South Australia, Australia, which global-law reads at `australia/south-australia/acts/`.

The Acts of South Australia as a git repository: 581 Acts in force, 2851 versions. 267 of them have every
consolidated version the [South Australian Legislation website](https://www.legislation.sa.gov.au) publishes as RTF
(1805 versions); 314 more come from the South Australian Government's open data on
[data.sa.gov.au](https://data.sa.gov.au/data/dataset/database-update-package-xml), from 2017 on (1046 versions;
see "Acts added from the open data"). Each amending Act is one commit, credited to that Act and dated the day it took
effect, at noon in Adelaide.

**This record is partial.** It holds 581 of the 1,390 Acts on the A-Z list of Acts in force (many of the rest are
Acts of limited application, repealing Acts and old titles with no consolidated text). More Acts are appended as they
are checked; nothing already published is rewritten.

## The questions, as git commands

| Question | Command |
| --- | --- |
| What did a section say on a day? | `python tools/in_force_on.py 2015-06-01 constitution-act-1934 s-028` |
| The whole Act on a day | `python tools/in_force_on.py 2015-06-01 constitution-act-1934` |
| Which Act wrote each line of a section? | `git blame constitution-act-1934/s-028.md` |
| Every change to a section, with the Act and the day | `git log --follow constitution-act-1934/s-028.md` |
| Everything one Act changed | `git log --author="(No. 40 of 2021)" --stat` |
| A version of an Act, as published | `git show constitution-act-1934/2021-11-04:constitution-act-1934/s-006.md` (tags `<act>/<first day>`) |
| What changed between two versions | `git diff --stat constitution-act-1934/2017-12-12 constitution-act-1934/2021-01-01 -- constitution-act-1934` |
| The commit that holds a day's text, and why | `python tools/in_force_on.py 2015-06-01 constitution-act-1934 --commit` |
| Every change with its Act and day | [`changes.csv`](changes.csv) |
| Changes two Acts could have made (credited to neither) | `git log --grep="Kind: two or more Acts"` |
| Changes credited by date only | `git log --grep="by date only"` |
| Changes no Act explains | `git log --grep="Kind: no Act named"` |

## Acts added from the open data

314 Acts were added after the first 267, from the open data of the Office of Parliamentary Counsel on
data.sa.gov.au: the Legislative Database of 17 November 2017 (every Act then in operation, as RTF) and every
fortnightly Legislative Database Update Package (XML) since (each Act substituted or added in the fortnight, as a whole
version with the day it took effect). They are read and credited as the first 267: each version's legislative
history names the Act that changed each provision, with its commencement. For these Acts:

- the history starts with the version in force on 17 November 2017 (for an Act passed later, its first version in the
  packages); amendments before it are in that text and not separated out;
- a version replaced within a fortnight, before the next package, is not in the packages: its changes fall into the
  next version;
- the commits were appended to main after those of the first 267 Acts, because main only grows: they come after
  commits with later dates. Each commit's day is in its `In-force:` trailer, and `tools/in_force_on.py` reads an Act's
  text on a day from the commits that touch that Act. `git rev-list --before` (committer dates) answers for the first
  267 Acts only; for the others use the tool, or `git log -- <act>/` and the trailers;
- the 2017 versions are RTF and the later ones XML. Where a provision differs between two versions only in white space,
  table bars or a non-breaking hyphen for a hyphen (the two formats print a few spaces differently), it keeps the earlier version's text, so a change of
  format is never recorded as a change of law;
- the first 25 of them were appended Act by Act; the rest as one block: the first version of every Act (A-Z), then
  every later change of all of them, in day order. The version tags `<act>/<first day>` point at the commit that
  holds that version.

`tools/saopen.py` reads the open data, `tools/saxml.py` the XML versions (written to print what `tools/sartf.py` prints
for the same version's RTF), `tools/saappend.py` appends, and `tools/check_open.py` checks every version of every Act
on main, file by file and, with a separate reader, letter by letter.

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

Counts in this build: 1159 law commits (act: 761, candidates: 31, editorial: 69, start: 267, unexplained: 31),
1805 version tags.

## How it is checked

`tools/check_repo.py` reads every version of every Act back from git (its tag) and compares it with the official RTF,
file by file; `tools/check_words.py` compares the letters and digits of every version with the raw RTF read by a
separate minimal extractor; `tools/test_repo.py` checks that the commands above work. Results are in
[`REPRODUCE.md`](REPRODUCE.md). Since Acts were appended, `tools/check_open.py` does the same for every Act on main, choosing each version's commit by the Act's own commits and In-force trailers.

## Files

Each Act is a folder named as on the site (`constitution-act-1934/`). Each section is one file: `s-028.md` is section 28, `s-028A.md`
section 28A. A schedule is a folder: `sch-1/schedule.md` holds its heading and any text before its clauses,
`sch-1/cl-003.md` its clause 3. `title.md` is the title and long title; `headings.md` lists the chapter, part and
division headings in order. In each file the first line is the section's number and heading, then one line per
paragraph (subsection, paragraph, definition, note, penalty). Table cells are separated by `│`. A repealed section
is removed.

## What it does not show

- Acts appended from the open data: versions before the one in force on 17 November 2017, and versions replaced within a fortnight.
- Versions published only as PDF (505 older versions of these Acts, mostly before 2003), Acts that have ceased,
  Acts with no consolidated version on the site, and regulations.
- The text on a day between two versions other than as rebuilt from the Acts' commencements (above).
- Which of two Acts of one period made a change, where both are new in the history (above).
- The legislative history tables themselves, the tables of contents and footnote markers.

The authorised text is the one on the South Australian Legislation website.

## How it is built

`tools/build.py` writes this repository from scratch from the files retrieved from the site (listed with their
SHA-256 in [`DATA-SHA256SUMS`](DATA-SHA256SUMS)); the same files give the same hashes. See [`REPRODUCE.md`](REPRODUCE.md)
and [`corpus.json`](corpus.json). After a rebuild, `git fetch --force --tags` replaces stale tags in an existing clone.

## Licence

Based on content from the South Australian Legislation website at 29 October 2026. For the latest information on South Australian Government legislation, please go to www.legislation.sa.gov.au/ .

Despite any headers, footers or other markings, this legislation is NOT published under the Legislation Revision and Publication Act 2002. To access legislation that is published under the Legislation Revision and Publication Act 2002, please go to www.legislation.sa.gov.au/ .

The Acts added from the open data: Office of Parliamentary Counsel, Government of South Australia, Legislative Database Update Package (XML) and Legislative Database, sourced on 2026-11-02, https://data.sa.gov.au/data/dataset/database-update-package-xml and https://data.sa.gov.au/data/dataset/legislative-database. Licensed under the Creative Commons Attribution 4.0 Licence (data.sa.gov.au/copyright); the dataset is published under CC BY 4.0. The South Australian Government does not endorse this repository.

The content is licensed by the State of South Australia under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/);
it has been changed (split into files by provision, white space normalised, history reconstructed). See
[`LICENSE`](LICENSE) and [`SOURCES.md`](SOURCES.md). **This repository is private.**
