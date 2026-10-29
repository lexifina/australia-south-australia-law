"""Checks every version of every Act in the repository against the official RTF it was built from.

    python -X utf8 check_repo.py [--repo PATH] [--limit N] [--today YYYY-MM-DD] [--workers 8]

The Acts checked are exactly those the build includes: the same selection (`--limit`, the first N current Acts of the
A-Z list, as given to build.py) and the same rule (an Act is built only when every RTF version is cached; one version
per first day). An Act that is cached but not fully fetched is not in the build and is not checked.

For each version in force on or before `--today`: the Act's files in the last main commit in force on or before the
version's first day (In-force trailer) must equal, byte for byte, the files parsed from that version's RTF; and the
version's tag `<act>/<first day>` must point at that same commit. The repository must hold no Act folder the build
does not include, and no version tag the build does not make. The independent word check is check_words.py. Writes
the result to verification.json ("check").
"""
from __future__ import annotations

import argparse
import bisect
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build  # noqa: E402

NOT_LAW = {'tools', 'data'}


class Blobs:
    """Reads blobs through one `git cat-file --batch` process."""

    def __init__(self, repo: Path):
        self.p = subprocess.Popen(['git', 'cat-file', '--batch'], cwd=repo, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE)

    def get(self, spec: str) -> bytes | None:
        self.p.stdin.write(spec.encode('utf-8') + b'\n')
        self.p.stdin.flush()
        head = self.p.stdout.readline().split()
        if len(head) < 3:
            return None
        n = int(head[2])
        data = self.p.stdout.read(n)
        self.p.stdout.read(1)
        return data

    def files(self, rev: str, folder: str) -> list[str]:
        """Every file path under folder/ in rev (recursively), read from the tree objects: no process per call."""
        out = []

        def walk(spec: str, prefix: str):
            data = self.get(spec)
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
                    out.append(prefix + name)
        walk(f'{rev}:{folder}', folder + '/')
        return out


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=repo, capture_output=True).stdout.decode('utf-8')


def tree_files(blobs: Blobs, rev: str, folder: str) -> list[str]:
    return [l for l in blobs.files(rev, folder) if not l.endswith('/README.md')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=str(HERE.parents[2] / 'australia-south-australia-law-build'))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--acts-file', default=None, help='the Acts of the build (default: .cache/built-acts.json)')
    ap.add_argument('--today', default='9999-12-31')
    ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()
    repo = Path(a.repo)
    titles = build.load_acts(build.select_acts(a.limit, a.acts_file or (str(build.BUILT) if build.BUILT.exists() else None)), a.workers)

    tags = dict(l.split(' ') for l in git(repo, 'for-each-ref', '--format=%(refname:short) %(objectname)',
                                           'refs/tags').splitlines() if l)
    log = git(repo, 'log', '--first-parent', '--reverse',
              '--format=%H %(trailers:key=In-force,valueonly,separator=)', 'main').splitlines()
    # the documentation commit carries In-force too (the build day); law commits only
    law = [l.split(' ') for l in log if len(l.split(' ')) == 2]
    docs = git(repo, 'log', '-1', '--format=%H', '--grep=^Kind: documentation', 'main').strip()
    law = [(h, d) for h, d in law if h != docs]
    days = [d for _h, d in law]
    order_ok = days == sorted(days)

    def on_day(day):
        i = bisect.bisect_right(days, day)
        return law[i - 1][0] if i else None

    blobs = Blobs(repo)
    bad, tag_off, missing_tags = [], [], []
    files = versions = 0
    want_tags = set()
    for t in titles:
        for v in t['versions']:
            if v['from'] > a.today:
                continue
            label = f'{t["slug"]}/{v["from"]}'
            rev = on_day(v['from'])
            if rev is None:
                bad.append({'version': label, 'problem': 'no commit in force on its first day'})
                continue
            versions += 1
            want_tags.add(label)
            if label not in tags:
                missing_tags.append(label)
            elif tags[label] != rev:
                tag_off.append(label)
            want = {f'{t["slug"]}/{build.file_name(k)}': txt for k, txt in v['doc']['units'].items()}
            have = tree_files(blobs, rev, t['slug'])
            files += len(want)
            if sorted(have) != sorted(want):
                bad.append({'version': label, 'missing': sorted(set(want) - set(have))[:5],
                            'extra': sorted(set(have) - set(want))[:5]})
                continue
            for p, txt in want.items():
                if blobs.get(f'{rev}:{p}') != txt.encode('utf-8'):
                    bad.append({'version': label, 'file': p})
                    break
    built = {t['slug'] for t in titles}
    top = {l for l in git(repo, 'ls-tree', '-d', '--name-only', 'main').splitlines() if l}
    extra_acts = sorted(top - built - NOT_LAW)
    missing_acts = sorted(built - top)
    extra_tags = sorted(set(tags) - want_tags)
    rec = {'acts': len(titles), 'versions_checked': versions, 'files': files, 'mismatches': len(bad),
           'examples': bad[:10], 'tags_not_on_their_day': len(tag_off), 'tag_examples': tag_off[:5],
           'tags_missing': len(missing_tags), 'tags_extra': len(extra_tags), 'extra_tag_examples': extra_tags[:5],
           'acts_missing': missing_acts[:10], 'acts_not_in_build': extra_acts[:10], 'in_force_order': order_ok}
    print(json.dumps(rec, indent=1))
    ok = not (bad or tag_off or missing_tags or extra_tags or extra_acts or missing_acts or not order_ok)
    p = HERE / 'verification.json'
    ver = json.loads(p.read_text('utf-8')) if p.exists() else {}
    ver['check'] = {k: rec[k] for k in ('acts', 'versions_checked', 'files', 'mismatches', 'tags_not_on_their_day',
                                        'tags_missing', 'tags_extra')}
    p.write_text(json.dumps(ver, indent=1, sort_keys=True) + '\n', 'utf-8', newline='\n')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
