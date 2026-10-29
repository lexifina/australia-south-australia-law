"""Functional smoke test of the built South Australian law repository: the things a reader does with it work.
Standard library only.

    python -X utf8 scripts/australia-south-australia-law/test_repo.py [--repo PATH] [--record]
    python -X utf8 tools/test_repo.py                   # from inside the repository

It asserts that:
1. `git blame` on a provision changed by Acts names an amending Act as author of its lines;
2. tools/in_force_on.py gives the text before and after an amendment (for three provisions), and each equals the
   file at the commit that holds that day;
3. `git log --follow` on those files lists commits in the order they took effect, authored by Acts or the publisher;
4. every tag resolves to a commit and every tag `<act>/<day>` holds that Act's folder; every law commit has an
   `In-force:` trailer;
5. no commit is authored by Lexifina except the documentation commit (the last one), and Lexifina commits all;
6. every git or tool command in the README's table runs and prints something.
The example provisions are found in the data (the first provisions changed twice by Acts on different days), so the
test works on any build. With --record the result is written to verification.json next to this script.
"""
import argparse
import datetime as dt
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACT = re.compile(r'^.+ \(No\. \d+ of \d{4}\)$')
PUBLISHER = 'South Australian Legislation (Commissioner for Legislation Revision and Publication)'


def default_repo() -> Path:
    if (HERE.parent / '.git').exists():
        return HERE.parent
    return HERE.parents[2] / 'australia-south-australia-law-build'


class Smoke:
    def __init__(self, repo: Path):
        self.repo = repo
        self.results: list[tuple[str, bool, str]] = []
        self._examples = None

    def git(self, *args: str) -> str:
        out = subprocess.run(['git', *args], cwd=self.repo, capture_output=True, text=True, encoding='utf-8')
        if out.returncode:
            raise AssertionError(f'git {" ".join(args)}: {out.stderr.strip()}')
        return out.stdout

    def tool(self, *args: str) -> str:
        out = subprocess.run([sys.executable, 'tools/in_force_on.py', *args], cwd=self.repo, capture_output=True,
                             text=True, encoding='utf-8')
        if out.returncode:
            raise AssertionError(f'in_force_on {" ".join(args)}: {out.stderr.strip()}')
        return out.stdout

    def check(self, name: str, fn):
        try:
            detail = fn() or ''
            self.results.append((name, True, detail))
            print(f'PASS {name}: {detail}')
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            print(f'FAIL {name}: {exc}')

    def history(self, path: str) -> list[tuple[str, str, str]]:
        out = self.git('log', '--follow', '--format=%H%x09%an%x09%(trailers:key=In-force,valueonly,separator=)',
                       'main', '--', path)
        return [tuple(l.split('\t')) for l in out.splitlines() if l]

    def examples(self) -> list[tuple[str, list]]:
        """Three section files changed by Acts on at least two different days, from different Acts (folders)."""
        if self._examples is None:
            out = self.git('log', '--reverse', '--format=@%an', '--name-only', '--diff-filter=M', 'main')
            seen, picks, author = {}, [], ''
            for l in out.splitlines():
                if l.startswith('@'):
                    author = l[1:]
                elif re.search(r'/s-\d+[A-Za-z]*\.md$', l) and ACT.match(author):
                    seen[l] = seen.get(l, 0) + 1
                    folder = l.split('/')[0]
                    if seen[l] == 2 and folder not in {p.split('/')[0] for p, _ in picks}:
                        picks.append((l, self.history(l)))
                        if len(picks) == 3:
                            break
            assert picks, 'no section changed twice by Acts'
            self._examples = picks
        return self._examples

    # 1
    def blame(self):
        path, _ = self.examples()[0]
        out = self.git('blame', '--line-porcelain', 'main', '--', path)
        authors = {l[7:] for l in out.splitlines() if l.startswith('author ')}
        assert authors, 'no blame output'
        assert all(ACT.match(a) or a == PUBLISHER for a in authors), f'unexpected authors {authors}'
        assert any(ACT.match(a) for a in authors), f'no Act among the authors {authors}'
        return f'{path} lines by {", ".join(sorted(authors))}'

    # 2
    def on_date(self):
        done = []
        for path, rows in self.examples():
            act, prov = path.split('/', 1)
            prov = prov[:-3]
            # the newest change by an Act whose day differs from the change before it
            rows = rows[::-1]   # oldest first
            pick = None
            for k in range(1, len(rows)):
                if ACT.match(rows[k][1]) and rows[k][2].strip() > rows[k - 1][2].strip():
                    # the last commit of that day for this file
                    j = k
                    while j + 1 < len(rows) and rows[j + 1][2].strip() == rows[k][2].strip():
                        j += 1
                    pick = (rows[k - 1], rows[j])
            assert pick, f'{path}: no change by an Act on a new day'
            (h0, _a0, d0), (h1, _a1, d1) = pick
            d1 = d1.strip()
            before = (dt.date.fromisoformat(d1) - dt.timedelta(days=1)).isoformat()
            a, b = self.tool(before, act, prov), self.tool(d1, act, prov)
            assert b == self.git('show', f'{h1}:{path}'), f'{path} on {d1} is not the file at {h1[:10]}'
            if before >= d0.strip():
                assert a == self.git('show', f'{h0}:{path}'), f'{path} on {before} is not the file at {h0[:10]}'
            assert a != b, f'{path}: no change between {before} and {d1}'
            done.append(f'{path} {before} != {d1}')
        whole = self.tool('9999-12-31', self.examples()[0][0].split('/')[0])
        assert '=== ' in whole, 'whole Act printed nothing'
        return '; '.join(done)

    # 3
    def follow(self):
        checked = []
        for path, rows in self.examples():
            days = [d.strip() for _h, _a, d in rows]
            assert len(rows) >= 2, f'{path}: {len(rows)} commits'
            assert days == sorted(days, reverse=True), f'{path}: not in date order {days}'
            assert all(ACT.match(a) or a == PUBLISHER for _h, a, _d in rows), f'{path}: authors {[a for _h, a, _d in rows]}'
            checked.append(f'{path} {len(rows)} commits')
        return '; '.join(checked)

    # 4
    def tags_and_trailers(self):
        refs = self.git('for-each-ref', '--format=%(refname:short) %(objecttype)', 'refs/tags').splitlines()
        assert refs, 'no tags'
        bad = [r for r in refs if not r.endswith(' commit')]
        assert not bad, f'tags not on commits: {bad[:3]}'
        sample = refs[::max(1, len(refs) // 50)]
        for r in sample:
            t = r.split(' ')[0]
            act = t.rsplit('/', 1)[0]
            assert self.git('ls-tree', '--name-only', t, '--', act + '/').strip(), f'{t}: no {act}/ folder'
        rows = self.git('log', '--format=%H%x09%an%x09%(trailers:key=In-force,valueonly,separator=)%x09%s',
                        'main').splitlines()
        law = [r for r in rows if r.split('\t')[1] != 'Lexifina']
        missing = [r for r in law if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', r.split('\t')[2].strip())]
        assert not missing, f'{len(missing)} law commits without a valid In-force, e.g. {missing[:2]}'
        return f'{len(refs)} tags resolve ({len(sample)} checked for their folder); {len(law)} law commits carry In-force'

    # 5
    def lexifina(self):
        rows = self.git('log', '--format=%H%x09%an%x09%ae%x09%cn', 'main').splitlines()
        by_lex = [r for r in rows if r.split('\t')[1] == 'Lexifina']
        assert len(by_lex) == 1 and by_lex[0] == rows[0], f'{len(by_lex)} commits by Lexifina'
        assert all(r.split('\t')[3] == 'Lexifina' for r in rows), 'a committer other than Lexifina'
        assert all(r.split('\t')[2].endswith('.invalid') for r in rows[1:]), 'a law author without an .invalid address'
        authors = {r.split('\t')[1] for r in rows[1:]}
        assert all(ACT.match(a) or a == PUBLISHER for a in authors), f'unexpected authors {authors - {PUBLISHER}}'
        return f'only the documentation commit {rows[0][:10]} is by Lexifina; {len(authors)} distinct authors'

    # 6
    def readme(self):
        text = (self.repo / 'README.md').read_text(encoding='utf-8')
        table = text[text.index('## The questions'):text.index('## The dates')]
        cmds = [c for c in re.findall(r'`([^`]+)`', table) if c.startswith(('git ', 'python '))]
        assert len(cmds) >= 8, f'only {len(cmds)} commands found'
        for c in cmds:
            args = shlex.split(c)
            if args[0] == 'python':
                args[0] = sys.executable
            out = subprocess.run(args, cwd=self.repo, capture_output=True, text=True, encoding='utf-8')
            assert out.returncode == 0, f'{c}: exit {out.returncode} {out.stderr.strip()[:200]}'
            assert out.stdout.strip(), f'{c}: printed nothing'
        return f'{len(cmds)} README commands ran and printed output'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=None)
    ap.add_argument('--record', action='store_true')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    s = Smoke(Path(args.repo) if args.repo else default_repo())
    s.check('blame names an Act', s.blame)
    s.check('text before and after an amendment', s.on_date)
    s.check('log --follow in date order', s.follow)
    s.check('tags resolve, In-force on every law commit', s.tags_and_trailers)
    s.check('only the documentation commit is by Lexifina', s.lexifina)
    s.check('README commands run', s.readme)
    ok = all(r[1] for r in s.results)
    print(f'{sum(r[1] for r in s.results)}/{len(s.results)} passed')
    if args.record:
        p = HERE / 'verification.json'
        rec = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        rec['smoke'] = {'passed': sum(r[1] for r in s.results), 'total': len(s.results)}
        p.write_text(json.dumps(rec, indent=1, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8',
                     newline='\n')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
