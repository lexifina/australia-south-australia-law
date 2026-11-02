"""Reads the South Australian legislation open data on data.sa.gov.au, as downloaded (never the network):

- the "Legislative Database" snapshot of 17 November 2017 (Acts, as RTF: every Act then in operation), and
- every fortnightly "Legislative Database Update Package (XML)" since: each holds the Acts substituted, added, ceased
  or renamed since the one before, as whole exchange-XML versions, with an instruction sheet naming them.

    python -X utf8 saopen.py index      (writes <data>/index.json: every Act file in every package)

The data folder is SA_OPENDATA (default: tools/.cache/opendata): base/ holds the snapshot ZIP, xml/ the update
packages named <publication day>_<resource id>.zip, and xml/resources.json the dataset's resource list.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('SA_OPENDATA', HERE / '.cache' / 'opendata'))
BASE_ZIP = 'base/2017-11-17-a-p-pol.zip'
BASE_DAY = '2017-11-17'
DATASET_XML = 'https://data.sa.gov.au/data/dataset/database-update-package-xml'
DATASET_BASE = 'https://data.sa.gov.au/data/dataset/legislative-database'
BASE_URL = ('https://data.sa.gov.au/data/dataset/675635f9-6831-457a-ba2e-52b4d49701f5/resource/'
            '5cd81968-43a6-4d72-b185-c62b5b1e5d18/download/a-p-pol.zip')


def packages() -> list[dict]:
    """The XML update packages, oldest first: day, file, resource id and URL."""
    res = json.loads((DATA / 'xml' / 'resources.json').read_text('utf-8'))
    out = []
    for f in sorted((DATA / 'xml').glob('*.zip')):
        day, rid = f.stem.split('_', 1)
        r = next(x for x in res if x['id'].startswith(rid))
        out.append({'day': day, 'file': f.name, 'id': r['id'], 'url': r['url']})
    return out


def sheet_text(z: zipfile.ZipFile) -> str:
    """The instruction sheet of a package, as text (pdftotext)."""
    name = next((n for n in z.namelist() if n.lower().startswith('updatedatabaseinstructionsheet')), None)
    if not name:
        return ''
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / 'sheet.pdf'
        p.write_bytes(z.read(name))
        r = subprocess.run(['pdftotext', '-layout', str(p), '-'], capture_output=True)
        return r.stdout.decode('utf-8', 'replace')


def sheet_items(text: str) -> dict:
    """{'added': [...], 'substituted': [...], 'ceased': [...], 'renamed': [...]} of A\\ items (Acts) on a sheet."""
    out = {'added': [], 'substituted': [], 'ceased': [], 'renamed': []}
    cur = None
    for line in text.splitlines():
        l = line.strip()
        m = re.search(r'have been (added|substituted|renamed)|have ceased operation', l)
        if m:
            cur = m.group(1) or 'ceased'
            continue
        if l.startswith('The following can be found'):
            cur = None
        if cur and l.startswith('A\\'):
            out[cur].append(re.sub(r'\s+', ' ', l[2:]).strip())
    return out


HEAD = re.compile(rb'<exdoc ([^>]*)>')


def attrs(raw: bytes) -> dict:
    m = HEAD.search(raw[:4000])
    return {k.decode(): v.decode('ascii', 'replace') for k, v in re.findall(rb'([\w.]+)="([^"]*)"', m.group(1))} if m else {}


def index() -> dict:
    files = []
    sheets = {}
    for p in packages():
        z = zipfile.ZipFile(DATA / 'xml' / p['file'])
        sheets[p['day']] = sheet_items(sheet_text(z))
        if 'A.zip' not in z.namelist():
            continue
        a = zipfile.ZipFile(io.BytesIO(z.read('A.zip')))
        for n in sorted(a.namelist()):
            if not n.endswith('.xml'):
                continue
            raw = a.read(n)
            h = attrs(raw)
            files.append({'pkg': p['file'], 'pkg_day': p['day'], 'member': n, 'folder': n.split('/')[0],
                          'sub': n.split('/')[1] if n.count('/') >= 2 else '',
                          'year': h.get('year'), 'number': h.get('number'),
                          'from': h.get('first.valid.date'), 'doc_class': h.get('doc.class'),
                          'sha256': hashlib.sha256(raw).hexdigest(), 'url': p['url']})
    base = []
    z = zipfile.ZipFile(DATA / BASE_ZIP)
    for n in sorted(z.namelist()):
        if n.startswith('A/') and n.lower().endswith('.rtf'):
            raw = z.read(n)
            m = re.match(r'(\d{4})\.([^.]+)\.', n.rsplit('/', 1)[-1])
            base.append({'member': n, 'folder': n.split('/')[1], 'year': m.group(1) if m else None,
                         'number': m.group(2) if m else None, 'sha256': hashlib.sha256(raw).hexdigest()})
    out = {'files': files, 'sheets': sheets, 'base': base}
    (DATA / 'index.json').write_text(json.dumps(out, indent=0, sort_keys=True) + '\n', 'utf-8')
    return out


def load_index() -> dict:
    return json.loads((DATA / 'index.json').read_text('utf-8'))


_zips: dict = {}


def read_member(pkg: str, member: str) -> bytes:
    if pkg not in _zips:
        z = zipfile.ZipFile(DATA / 'xml' / pkg)
        _zips[pkg] = zipfile.ZipFile(io.BytesIO(z.read('A.zip')))
    return _zips[pkg].read(member)


def read_base(member: str) -> bytes:
    if BASE_ZIP not in _zips:
        _zips[BASE_ZIP] = zipfile.ZipFile(DATA / BASE_ZIP)
    return _zips[BASE_ZIP].read(member)


if __name__ == '__main__':
    if sys.argv[1:2] == ['index']:
        ix = index()
        print(len(ix['files']), 'xml files;', len(ix['base']), 'base RTFs;', len(ix['sheets']), 'packages')
