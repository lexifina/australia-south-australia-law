"""The independent word check: every version's files, as stored in git, against the letters and digits of the raw RTF.

    python -X utf8 check_words.py [--repo PATH] [--limit N] [--today YYYY-MM-DD] [--workers 8]

The RTF is read by `rtf_letters` below, a separate minimal extractor that shares no code with the build (`sartf.py`,
`build.read_version`): it walks the RTF groups, skips the destinations that are not body text (font, colour and style
tables, document info, headers, footers, pictures, field instructions, `{\\*...}` groups), decodes `\\'hh` (cp1252)
and `\\uN`, drops superscript digits (footnote markers), and keeps only letters and digits, case-folded. No styles,
paragraphs or tables.

For each version (the same selection as the build and check_repo.py: `--limit`, versions in force by `--today`):
- `title.md` must be found in the source, and every other file must follow it so that, end to end in some order,
  the files tile the source exactly from the body's start to the "Legislative history" heading;
- `headings.md` is split into its lines, each a separate piece (a heading inside a schedule carries the build's
  ` [sch-N]` marker, which is not source text and is removed first);
- the region skipped between the title and the body must be the table of contents, so it must begin with
  "contents".
If the build left out, invented or moved any letter, the check fails. Writes the result to verification.json ("words").
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- the independent extractor

_SKIP = {'fonttbl', 'colortbl', 'stylesheet', 'info', 'header', 'headerl', 'headerr', 'headerf', 'footer', 'footerl',
         'footerr', 'footerf', 'pict', 'fldinst', 'datafield', 'listtable', 'listoverridetable', 'rsidtbl',
         'generator', 'xmlnstbl', 'latentstyles', 'themedata', 'colorschememapping', 'datastore', 'object',
         'shp', 'shpinst', 'nonshppict', 'footnote', 'annotation', 'listtext', 'pntext', 'pntxta', 'pntxtb', 'tc',
         'xe', 'bkmkstart', 'bkmkend', 'revtbl', 'pgdsctbl', 'atnid', 'atnauthor'}
_KEEP = re.compile(r'[^\W_]+')
_TOK = re.compile(r"\\([a-z]+)(-?\d+)? ?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|([^\\{}]+)", re.I | re.S)


def letters(s: str) -> str:
    return ''.join(_KEEP.findall(s.casefold()))


def rtf_letters(raw: bytes) -> str:
    text = raw.decode('latin-1')
    out = []
    # state per group: [skipping, superscript, unicode skip count]
    state = [False, False, 1]
    stack = []
    fresh = False          # just after '{' (a '\*' there marks an ignorable destination)
    skip_chars = 0
    for m in _TOK.finditer(text):
        word, num, hx, sym, brace, plain = m.groups()
        if brace == '{':
            stack.append(state[:])
            fresh = True
            continue
        if brace == '}':
            if stack:
                state = stack.pop()
            fresh = False
            skip_chars = 0
            continue
        was_fresh, fresh = fresh, False
        if sym == '*' and was_fresh:
            state[0] = True
            continue
        if word is not None:
            w = word.lower()
            if w in _SKIP:
                state[0] = True
            elif w in ('super', 'up'):
                state[1] = num is None or int(num) != 0
            elif w in ('nosupersub', 'plain'):
                state[1] = False
            elif w == 'uc':
                state[2] = int(num)
            elif w == 'u' and not state[0]:
                n = int(num)
                out.append((chr(n + 65536 if n < 0 else n), state[1]))
                skip_chars = state[2]
            continue
        if hx is not None:
            if skip_chars:
                skip_chars -= 1
                continue
            if not state[0]:
                out.append((bytes([int(hx, 16)]).decode('cp1252', 'replace'), state[1]))
            continue
        if sym is not None:
            continue
        if plain is not None:
            s = plain.replace('\r', '').replace('\n', '')
            if skip_chars:
                k = min(skip_chars, len(s))
                s, skip_chars = s[k:], skip_chars - k
            if s and not state[0]:
                out.append((s, state[1]))
    # superscript runs that are only digits are footnote markers
    kept = []
    i = 0
    while i < len(out):
        if out[i][1]:
            j = i
            run = []
            while j < len(out) and out[j][1]:
                run.append(out[j][0])
                j += 1
            r = ''.join(run)
            if not r.strip().isdigit():
                kept.append(r)
            i = j
        else:
            kept.append(out[i][0])
            i += 1
    return letters(''.join(kept))


# ---------------------------------------------------------------- tiling

def tile(src: str, title_lines: list[str], pieces: list[str]) -> tuple[bool, str]:
    """Do the pieces, end to end in some order, fill the source from the body's start to the history heading?
    The leading lines of title.md (title, long title) must open the source; its later lines (the enacting words,
    which follow the contents) are pieces of the body."""
    title_lines = [l for l in title_lines if l]
    t0 = src.find(title_lines[0]) if title_lines else -1
    if t0 < 0:
        return False, 'title not found in the source'
    title = ''
    k = 0
    while k < len(title_lines) and src.startswith(title + title_lines[k], t0):
        title += title_lines[k]
        k += 1
    pieces = [p for p in pieces + title_lines[k:] if p]
    after_title = t0 + len(title)
    ends = [m.start() for m in re.finditer('legislativehistory', src)][::-1]
    details = []   # (files left, detail) per phrase tried; the one that got furthest is reported
    for end in ends:
        if end < after_title:
            continue
        ok, detail, pos = _walk_back(src, pieces, after_title, end)
        if not ok:
            details.append((int(detail.split(' ', 1)[0]), detail))
            continue
        # what is left between the title and the first piece must be the table of contents: it starts with
        # "contents" and its last entries are the legislative history and, in some versions, an appendix
        toc = src[after_title:pos]
        h = toc.rfind('legislativehistory')
        if toc and not (toc.startswith('contents') and h >= 0 and
                        (h + 18 == len(toc) or toc[h + 18:].startswith('appendix'))):
            details.append((0, f'the {len(toc)} letters between the title and the body are not the table of '
                               f'contents: {toc[:40]!r} ... {toc[-60:]!r}'))
            continue
        return True, f'{sum(len(p) for p in pieces) + len(title)} letters'
    return False, min(details)[1] if details else 'no "legislative history" heading'


def _walk_back(src: str, pieces: list[str], floor: int, end: int) -> tuple[bool, str, int]:
    """Consume the pieces backwards from `end`, each time the longest piece the source ends with; every piece must
    be used, and none may reach above `floor` (the end of the title)."""
    left = {}
    for p in pieces:
        left.setdefault(p[-12:], []).append(p)
    pos = end
    n = len(pieces)
    while n:
        cands = [p for k, ps in left.items() if src.endswith(k, floor, pos) for p in ps
                 if src.endswith(p, floor, pos)]
        if not cands:
            def common(p):
                k = 0
                while k < len(p) and pos - k - 1 >= floor and p[-k - 1] == src[pos - k - 1]:
                    k += 1
                return k
            near = max((p for ps in left.values() for p in ps), key=common, default='')
            k = common(near)
            return False, (f'{n} files left, and none ends where the source has {src[max(floor, pos - 50):pos]!r} '
                           f'(letter {pos}); nearest file {near[-30:]!r} agrees for its last {k} letters, then has '
                           f'{near[max(0, len(near) - k - 30):len(near) - k]!r} where the source has '
                           f'{src[max(floor, pos - k - 30):pos - k]!r}'), pos
        hit = max(cands, key=len)
        left[hit[-12:]].remove(hit)
        if not left[hit[-12:]]:
            del left[hit[-12:]]
        pos -= len(hit)
        n -= 1
    return True, '', pos


def _job(arg):
    label, path, files, url = arg
    raw = Path(path).read_bytes()
    src = rtf_letters(raw)
    title = [letters(l) for l in files.pop('title.md', '').splitlines()]
    pieces = []
    for name, txt in files.items():
        if name == 'headings.md':
            pieces += [letters(re.sub(r' \[[a-z]+(-[0-9A-Za-z]+)?\]$', '', l)) for l in txt.splitlines()]
        else:
            pieces.append(letters(txt))
    ok, detail = tile(src, title, pieces)
    return label, ok, detail, url


def main():
    sys.path.insert(0, str(HERE))
    import build   # the selection of Acts and versions only; the text comes from git and the raw RTF
    import sacache
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'australia-south-australia-law-build'))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--acts-file', default=None, help='the Acts of the build (default: .cache/built-acts.json)')
    ap.add_argument('--today', default='9999-12-31')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--only', default='', help='check only Acts whose folder starts with this (no record written)')
    a = ap.parse_args()
    repo = Path(a.repo)
    titles = build.load_acts(build.select_acts(a.limit, a.acts_file or (str(build.BUILT) if build.BUILT.exists() else None)), a.workers)
    from check_repo import Blobs   # the git reader only (one cat-file process for trees and blobs)
    reader = Blobs(repo)

    def blob(spec):
        data = reader.get(spec)
        return None if data is None else data.decode('utf-8')

    jobs = []
    missing = []
    for t in titles:
        if not t['slug'].startswith(a.only):
            continue
        for v in t['versions']:
            if v['from'] > a.today:
                continue
            tag = f'{t["slug"]}/{v["from"]}'
            names = [n for n in reader.files(tag, t['slug']) if not n.endswith('/README.md')]
            if not names:
                missing.append(tag)
                continue
            files = {n[len(t['slug']) + 1:]: blob(f'{tag}:{n}') for n in names}
            jobs.append((tag, str(sacache.cache_path(v['rtf'])), files, v['rtf']))
    bad = []
    with cf.ProcessPoolExecutor(max_workers=a.workers) as ex:
        for label, ok, detail, url in ex.map(_job, jobs, chunksize=4):
            if not ok:
                bad.append({'version': label, 'detail': detail, 'rtf': url})
    rec = {'versions_checked': len(jobs), 'failed': len(bad), 'examples': bad[:10], 'tags_missing': len(missing),
           'missing_examples': missing[:5]}
    print(json.dumps(rec, indent=1, ensure_ascii=False))
    p = HERE / 'verification.json'
    ver = json.loads(p.read_text('utf-8')) if p.exists() else {}
    if a.only:
        raise SystemExit(0 if not bad and not missing else 1)
    ver['words'] = {k: rec[k] for k in ('versions_checked', 'failed', 'tags_missing')}
    p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', 'utf-8', newline='\n')
    raise SystemExit(0 if not bad and not missing else 1)


if __name__ == '__main__':
    main()
