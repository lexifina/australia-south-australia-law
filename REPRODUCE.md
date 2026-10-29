# Reproducing this repository

Everything here can be rebuilt from public data with the tools in `tools/`.

## Steps

1. Put the retrieved files from www.legislation.sa.gov.au (the Acts A-Z lists, each Act's page and the RTF of every
   version) where `tools/sacache.py` reads them, each RTF matching its SHA-256 in `DATA-SHA256SUMS`. How the files
   were retrieved is not part of this record.
2. `python -X utf8 tools/build.py --today 2026-10-29 --limit 445` writes the repository from the cache. `--today` is required: it
   dates the documentation commit and decides which versions are in force (later ones would go on a `future` branch).
3. `python -X utf8 tools/check_repo.py --today 2026-10-29 --limit 445` and `python -X utf8 tools/check_words.py --today 2026-10-29 --limit 445`
   compare every version with its RTF; `python -X utf8 tools/test_repo.py` runs the README's commands;
   `python -X utf8 tools/build.py --today 2026-10-29 --limit 445 --verify-rebuild` rebuilds into a temporary folder and compares hashes.

The RTF files the build read are listed with their SHA-256 in [`DATA-SHA256SUMS`](DATA-SHA256SUMS); the site replaces
the `current` file of an Act when a new version is published, so files retrieved later can differ.

## This build

- Build day: 2026-10-29; data retrieved 2026-10-28 to 2026-10-29.
- 267 Acts, 1805 versions, 1159 law commits + 1 documentation commit, 1805 tags,
  0 commits on `future`.
- Software: Python 3.12.10, git version 2.46.0.windows.1; no third-party packages (the RTF reader is `tools/sartf.py`).
- Verification: {"check": {"acts": 267, "files": 176894, "mismatches": 0, "tags_extra": 0, "tags_missing": 0, "tags_not_on_their_day": 0, "versions_checked": 1805}, "smoke": {"passed": 6, "total": 6}, "words": {"failed": 0, "tags_missing": 0, "versions_checked": 1805}}

## Decisions made by hand

- Section files are found by the RTF paragraph styles the site's publishing system writes (`clausehead...`,
  `parthead...`, `schedulehead`); schedule headings without a style are found by their text ("Schedule 1—").
- The body starts after the table of contents (whose last entry links to the legislative history).
- Attribution rules are in README.md. History rows listed under a section (definitions, "cl 3" under a schedule) are
  read as rows of that section or schedule.
- Footnote markers (superscript numbers) are left out.

## AI disclosure

The RTF reader, builder and checks were written by an AI coding agent (Claude, model claude-opus-5-5) for
Lexifina. No model reads or writes legal text at build time: every word comes from the RTF files.
