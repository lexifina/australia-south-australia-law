# South Australia law, change by change

The Acts of South Australia as a git repository: 267 Acts in force, each with every consolidated version the
[South Australian Legislation website](https://www.legislation.sa.gov.au) publishes as RTF (1805 versions). Each
amending Act is one commit, credited to that Act and dated the day it took effect, at noon in Adelaide.

This build covers the first 445 of the 1390 Acts in force on the site's A-Z list: 267 have consolidated text; 169 have none on the site (Acts of limited
application, repealing Acts and old titles, whose pages list no version); 3 could not be read.

**This record is partial.** It covers the first 445 of the 1,390 current Acts (267 with text). Later rebuilds will add the other Acts and re-date the history, so commit hashes and tags will change.

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
[`REPRODUCE.md`](REPRODUCE.md).

## Files

Each Act is a folder named as on the site (`constitution-act-1934/`). Each section is one file: `s-028.md` is section 28, `s-028A.md`
section 28A. A schedule is a folder: `sch-1/schedule.md` holds its heading and any text before its clauses,
`sch-1/cl-003.md` its clause 3. `title.md` is the title and long title; `headings.md` lists the chapter, part and
division headings in order. In each file the first line is the section's number and heading, then one line per
paragraph (subsection, paragraph, definition, note, penalty). Table cells are separated by `│`. A repealed section
is removed.

## What it does not show

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

The content is licensed by the State of South Australia under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/);
it has been changed (split into files by provision, white space normalised, history reconstructed). See
[`LICENSE`](LICENSE) and [`SOURCES.md`](SOURCES.md). **This repository is private.**
