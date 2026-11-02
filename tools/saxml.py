"""Reads one South Australian legislation exchange XML file (the "Legislative Database Update Package (XML)" on
data.sa.gov.au, DTD "-//SAOPC//DTD Exchange//EN") into the same shape build.read_version gives for an RTF version:
units (title, headings, s-NNN, sch-N/schedule, sch-N/cl-NNN ...), the Part path of each section, the version's first
day, and the two legislative history tables (principal and amending Acts; provisions amended).

Text is rendered as the RTF reader renders it: one paragraph per line; a number and its text on one line, joined by a
space; line breaks inside a paragraph and runs of spaces collapse to one space. Nothing here touches the network.
"""
from __future__ import annotations

import re
from xml.etree import ElementTree as ET

import build as B

# elements whose content is a paragraph of their own (structure, not inline text)
STRUCT = {'level', 'tier', 'block', 'list', 'li', 'deflist', 'head', 'table', 'tgroup', 'tbody', 'thead', 'row',
          'entry', 'note', 'content', 'graphicblock', 'formulablock', 'metadata', 'colspec', 'fragment'}
SKIP = {'metadata', 'colspec', 'graphic', 'parentattributes', 'attrib'}   # <field> is text ("$10 000.")
PREFIX_TIERS = {'penalty', 'expiationfees'}
LEVEL_WORD = {'chapter': 'Chapter', 'part': 'Part', 'division': 'Division', 'subdivision': 'Subdivision'}


def inline(e: ET.Element) -> str:
    """The text of an inline element and its inline children (b, i, legref, intref, autovalue, defterm, ...)."""
    out = [e.text or '']
    prev = None
    for c in e:
        if c.tag in SKIP:
            pass
        elif c.tag in STRUCT:
            out.append(' ' + inline(c) + ' ')
        elif c.tag == 'sup' and inline(c).strip().isdigit():
            pass   # a footnote marker (superscript number): the RTF reader drops it too
        else:
            if c.tag == 'defterm' and ''.join(out)[-1:] not in ('', ' ', '\n', '(', '\u201c', '"', '\u2018'):
                out.append(' ')   # "subsection (2)(e)" then a defined term: the RTF has the space
            if c.tag == 'citation' and prev is not None and prev.tag == 'name' and not (prev.tail or ''):
                out.append(' ')   # a case name and its citation: "R v Hinks [2000] 4 All ER 833"
            if c.tag == 'autovalue' and prev is not None and prev.tag == 'autovalue' and not (prev.tail or '')                     and inline(c)[:1].isalnum():
                out.append(' ')   # "Part 3" "Division 11A": two references printed as "Part 3 Division 11A"
            out.append(inline(c))
        tail = c.tail or ''
        if c.tag == 'defterm' and tail[:1].isalnum():
            tail = ' ' + tail   # "child" then "means": the RTF has the space
        out.append(tail)
        prev = c
    return ''.join(out)


GAZETTE_PAGE = re.compile(r'(Gazette (?:No: ?\S+ )?\d{1,2}\.\d{1,2}\.\d{4}) p (\d+) ?(?=\)|$|[ ,;.])')


def clean(s: str) -> str:
    s = s.replace('\r', ' ').replace('\n', ' ')
    s = B.render(s)
    if 'Gazette' in s:
        # a Gazette reference as the RTF prints it: "(see Gazette 25.10.2001 p4686)"
        s = GAZETTE_PAGE.sub(lambda m: f'{m.group(1)} p{m.group(2)}', s).replace(' )', ')')
    return s


class Doc:
    def __init__(self):
        self.units: dict[str, list[str]] = {}
        self.order: list[str] = []
        self.paths: dict[str, str] = {}

    def add(self, key: str) -> str:
        if key in self.units:
            n = 2
            while f'{key}-{n}' in self.units:
                n += 1
            key = f'{key}-{n}'
        self.units[key] = []
        self.order.append(key)
        return key


def first_para(e: ET.Element) -> str | None:
    """'txt' or 'list': which comes first in a tier after its head (a penalty followed by a list is its own line)."""
    for x in e.iter():
        if x.tag in ('head', 'heading'):
            continue
        if x.tag in ('txt', 'list'):
            return x.tag
    return None


def head_parts(e: ET.Element) -> tuple[str, str]:
    """(number, heading) of a level's or tier's <head> (a note has its <heading> directly)."""
    h = e.find('head')
    if h is None:
        hd = e.find('heading')
        return '', clean(inline(hd)) if hd is not None else ''
    no = h.find('no')
    hd = h.find('heading')
    return (clean(inline(no)) if no is not None else '', clean(inline(hd)) if hd is not None else '')


def entry_text(en: ET.Element) -> str:
    """A table cell: its paragraphs (with their numbers, "(b) ...") on one line."""
    d = Doc()
    r = Reader(d)
    r.cur = d.add('cell')
    for c in en:
        r.walk(c)
    r.flush()
    return clean(' '.join(d.units['cell']))


class Reader:
    def __init__(self, doc: Doc):
        self.d = doc
        self.cur = None
        self.prefix: list[str] = []   # numbers waiting for the next paragraph ("(1)", "(a)")

    def para(self, text: str):
        t = clean(' '.join(self.prefix + [text]))
        self.prefix = []
        if t:
            self.d.units[self.cur].append(t)

    def flush(self):
        if self.prefix:
            self.para('')

    def walk(self, e: ET.Element):
        """Paragraphs of a block-level element, in document order."""
        tag = e.tag
        if tag in SKIP:
            return
        if tag == 'txt':
            self.para(inline(e))
            return
        if tag == 'tier' and e.get('type') in PREFIX_TIERS or tag == 'note':
            no, hd = head_parts(e)
            lab = ' '.join(x for x in (no, hd) if x)
            if lab and tag == 'note':
                self.para(lab + '\u2014')
            elif lab and first_para(e) == 'txt':
                self.prefix.append(lab)
            elif lab:
                self.para(lab)
            for c in e:
                if c.tag not in ('head', 'heading'):
                    self.walk(c)
            self.flush()
            return
        if tag == 'head':
            no, hd = (clean(inline(e.find('no'))) if e.find('no') is not None else '',
                      clean(inline(e.find('heading'))) if e.find('heading') is not None else '')
            if no and not hd:
                self.prefix.append(no)
            elif no or hd:
                self.para(f'{no}\u2014{hd}' if no and hd else (no or hd))
            for more in e.findall('heading')[1:]:
                self.para(inline(more))  # a second heading ("ANNEX", then "UNIFORM LAW ..."): a line of its own
            sub = e.find('subheading')
            if sub is not None:
                self.para(inline(sub))   # "(Convention of October 26, 1973)": a line of its own
            return
        if tag == 'no':
            self.prefix.append(clean(inline(e)))
            return
        if tag == 'row':
            self.flush()
            cells = [entry_text(en) for en in e.findall('entry')]
            parts = []
            for c in cells:   # as the RTF reader prints a table row: "cell \u2502 cell \u2502" (an empty cell leaves its bar)
                parts += ([c] if c else []) + ['\u2502']
            self.para(' '.join(parts))
            return
        if tag in ('b', 'i', 'u', 'sup', 'sub', 'legref', 'intref', 'name', 'defterm', 'extref', 'citation'):
            self.para(inline(e))
            return
        if e.text and e.text.strip():
            self.para(e.text)
        for c in e:
            self.walk(c)
            if c.tail and c.tail.strip():
                self.para(c.tail)
        self.flush()


def read(raw: bytes) -> dict:
    root = ET.fromstring(raw)
    d = Doc()
    r = Reader(d)
    r.cur = d.add('title')
    title = clean(root.get('title', '').replace('&#x00A0;', '\u00a0'))
    d.units['title'] += ['South Australia', title]
    headings = d.add('headings')
    front = body = scheds = hist = None
    for c in root.findall('content'):
        t = c.get('type')
        if t == 'front':
            front = c
        elif t == 'body':
            body = c
        elif t == 'schedules':
            scheds = c
    hist = root.find(".//content[@type='history']")
    if front is not None:
        for c in front:
            r.walk(c)
    path: list[str] = []

    def level(e: ET.Element, sched: str | None):
        nonlocal path
        t = e.get('type')
        no, hd = head_parts(e)
        if t in LEVEL_WORD:
            line = f'{no}\u2014{hd}' if hd else no
            if sched is not None:
                r.para(line)
            else:
                d.units[headings].append(line if sched is None else f'{line} [{sched}]')
                hm = B.LEVEL_HEAD.match(line)
                if hm and sched is None:
                    lvl = B.LEVELS[hm.group(1)]
                    path = [p for p in path if B.LEVELS[p.split(' ')[0]] < lvl] + [f'{B.LEVEL_ABBR[hm.group(1)]} {hm.group(2)}']
                r.cur = headings
            for c in e:
                if c.tag == 'head':
                    continue
                if c.tag == 'level':
                    level(c, sched)
                else:
                    r.walk(c)
            return
        if t == 'clause' and not hd and not re.match(r'\d+[A-Za-z]*$', no):
            # a "clause" numbered like a subclause, "(1)", with no heading: the RTF prints it as text of its unit
            if no:
                r.prefix.append(no)
            for c in e:
                if c.tag == 'head':
                    continue
                if c.tag == 'level':
                    level(c, sched)
                else:
                    r.walk(c)
            r.flush()
            return
        if t == 'clause':
            nm = re.match(r'(\d+[A-Za-z]*)$', no)
            if sched is None:
                key = f's-{B.num_key(nm.group(1))}' if nm else f'x-{B.slug(no + " " + hd)[:40]}'
                d.paths[key if nm else ''] = ' '.join(path)
            else:
                key = f'{sched}/cl-{B.num_key(nm.group(1))}' if nm else f'{sched}/x-{B.slug(no + " " + hd)[:40]}'
            r.cur = d.add(key)
            r.para(f'{no}\u2014{hd}' if no and hd else (no or hd))
            for c in e:
                if c.tag == 'head':
                    continue
                if c.tag == 'level':
                    level(c, sched)
                else:
                    r.walk(c)
            return
        if t in ('schedule', 'appendix'):
            m = re.match(r'(Schedule|Appendix)\s*(\S*)', no)
            word = m.group(1) if m else ('Schedule' if t == 'schedule' else 'Appendix')
            num = m.group(2) if m else ''
            if not hd:
                # a schedule with no heading (forms only, or an appendix): the RTF prints its number as a plain
                # paragraph of the unit before, and so does this reader
                r.para(no or word)
                if e.find('head') is not None and e.find('head').find('subheading') is not None:
                    r.para(inline(e.find('head').find('subheading')))
                for c in e:
                    if c.tag == 'head':
                        continue
                    if c.tag == 'level':
                        level(c, sched)
                    else:
                        r.walk(c)
                return
            base = ('sch' if word == 'Schedule' else 'app') + (f'-{num}' if num else '')
            r.cur = d.add(f'{base}/schedule')
            r.para(f'{no or word}\u2014{hd}')
            if e.find('head') is not None and e.find('head').find('subheading') is not None:
                r.para(inline(e.find('head').find('subheading')))
            for c in e:
                if c.tag == 'head':
                    continue
                if c.tag == 'level':
                    level(c, base)
                else:
                    r.walk(c)
            return
        if t == 'form':
            # the RTF keeps a form's heading out of the text (with the form's drawing); so does this reader
            for c in e:
                if c.tag == 'head':
                    continue
                if c.tag == 'level':
                    level(c, sched)
                else:
                    r.walk(c)
            return
        # forms, embedded documents and anything else: in the current unit, in order
        r.walk(e)

    for part in (body, scheds):
        if part is None:
            continue
        for c in part:
            if c.tag == 'level':
                level(c, None)
            else:
                r.walk(c)
    if not d.units[headings]:
        del d.units[headings]
        d.order.remove(headings)
    text = {k: '\n'.join(d.units[k]) + '\n' for k in d.order}
    principal, provisions = history(hist)
    fv = root.get('first.valid.date')
    return {'units': text, 'order': d.order, 'paths': d.paths, 'version_day': fv,
            'principal': principal, 'provisions': provisions, 'history_paras': 0,
            'title': title, 'year': root.get('year'), 'number': root.get('number'),
            'enacted': root.get('enact.or.made.date'), 'doc_class': root.get('doc.class'),
            'status': (root.find(".//metadata[@class='inforce']/metacontent[@class='status']").text
                       if root.find(".//metadata[@class='inforce']/metacontent[@class='status']") is not None else None)}


def history(hist: ET.Element | None) -> tuple[list[dict], list[dict]]:
    """The principal/amending table and the provisions-amended table, as build.read_history reads them from RTF."""
    if hist is None:
        return [], []
    paras = []
    for tier in hist.iter('tier'):
        t = tier.get('type')
        if t not in ('principalamending', 'provisionsamended'):
            continue
        paras.append(('historyhead', 'Principal Act and amendments' if t == 'principalamending' else 'Provisions amended', None))
        for row in tier.iter('row'):
            cells = []
            for en in row.findall('entry'):
                c = clean(' '.join(inline(x) for x in en.iter('txt')))
                c = re.sub(r'Gazette\s+No:\s*\S+\s+', 'Gazette ', c)
                c = re.sub(r'\bp\s+(\d+)', r'p\1', c).replace(' )', ')').replace('( ', '(')
                cells.append(c)
            paras.append(('row', '\u2502'.join(cells) + '\u2502', None))
    return B.read_history(paras)
