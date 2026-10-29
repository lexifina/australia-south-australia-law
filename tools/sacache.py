"""Reads the retrieved files of the South Australian Legislation website: where each file is kept, and what an Act's
page lists (its versions and their RTF documents). Used by build.py, check_words.py and reproduce.py; nothing here
touches the network.

Each retrieved file is kept as `<cache>/<h[:2]>/<h>` (h = sha1 of its URL), with `<h>.json` beside it holding the URL,
the HTTP status, the size and the day it was retrieved.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / '.cache'
SITE = 'https://www.legislation.sa.gov.au'
MAX_403 = 3          # a URL refused this many times (on separate passes) counts as a final answer


def cache_path(url: str) -> Path:
    h = hashlib.sha1(url.encode('utf-8')).hexdigest()
    return CACHE / h[:2] / h


def cached(url: str) -> bytes | None:
    """The retrieved body, b'' for a final negative answer, None when not (yet) retrieved."""
    p = cache_path(url)
    meta = p.with_suffix('.json')
    if not meta.exists():
        return None
    m = json.loads(meta.read_text('utf-8'))
    if m.get('status') == 403 and m.get('tries', 1) < MAX_403:
        return None   # not a final answer yet
    if m.get('status') != 200:
        return b''
    return p.read_bytes()


def failed(url: str) -> bool:
    """True when the server has given a final negative answer for the URL (404, 410, or refused MAX_403 times)."""
    meta = cache_path(url).with_suffix('.json')
    if not meta.exists():
        return False
    m = json.loads(meta.read_text('utf-8'))
    return m.get('status') != 200 and not (m.get('status') == 403 and m.get('tries', 1) < MAX_403)


def act_page_url(path: str) -> str:
    return SITE + '/lz?path=' + urllib.parse.quote(path, safe='/()\',')


# ---------------------------------------------------------------- an Act's page

_ROW = re.compile(r'<tr>(.*?)</tr>', re.S)


def versions(body: bytes) -> list[dict]:
    """Every version row: name (Current/Historical), the sub-name with its range, and its document links."""
    t = body.decode('utf-8', 'replace')
    out = []
    for m in _ROW.finditer(t):
        row = m.group(1)
        name = re.search(r'leg-table__name">([^<]*)<', row)
        if not name:
            continue
        sub = re.search(r'leg-table__sub-name">\s*([^<]*?)\s*<', row)
        links = re.findall(r'href="(https://www\.legislation\.sa\.gov\.au/_legislation-documents/[^"]+)"', row)
        out.append({'name': name.group(1).strip(), 'sub': sub.group(1).strip() if sub else '', 'links': links})
    return out


def act_meta(body: bytes) -> dict:
    t = body.decode('utf-8', 'replace')
    h1 = re.search(r'<h1>(.*?)</h1>', t, re.S)
    esi = re.search(r'ESI Path: (\S+?)\?SQ_DESIGN_NAME', t)
    return {'h1': html.unescape(h1.group(1)).strip() if h1 else '', 'doc_root': esi.group(1) if esi else ''}
