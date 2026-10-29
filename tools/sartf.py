"""Minimal RTF reader for SA Legislation consolidations (Elkera-generated RTF).

Returns a list of paragraphs: (style_name, text). Text keeps tabs as '\t'.
Headers, footers, field instructions, pictures, the font/colour/style tables are skipped.
"""
import re

_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([\r\n]+)|([^\\{}\r\n]+)", re.S)

_SKIP_DEST = {
    b"fonttbl", b"colortbl", b"stylesheet", b"info", b"header", b"footer", b"headerl", b"headerr",
    b"headerf", b"footerl", b"footerr", b"footerf", b"pict", b"fldinst", b"datafield", b"listtable",
    b"listoverridetable", b"rsidtbl", b"generator", b"xmlnstbl", b"latentstyles", b"themedata",
    b"colorschememapping", b"datastore", b"bkmkstart", b"bkmkend", b"object", b"shp", b"shpinst",
    b"nonshppict", b"footnote", b"annotation", b"atnid", b"atnauthor", b"revtbl", b"pgdsctbl",
    b"listtext", b"pntext", b"pntxta", b"pntxtb", b"tc", b"xe",
}


def styles(data: bytes):
    """style number -> style name from the stylesheet."""
    out = {}
    i = data.find(b"{\\stylesheet")
    if i < 0:
        return out
    depth = 0
    j = i
    while j < len(data):
        c = data[j:j + 1]
        if c == b"\\":
            j += 2
            continue
        if c == b"{":
            depth += 1
        elif c == b"}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    sheet = data[i:j]
    for m in re.finditer(rb"\{\\s(\d+)([^{}]*?)\s([^\\;{}]+);\}", sheet):
        out[int(m.group(1))] = m.group(3).decode("latin-1").strip()
    return out


def paragraphs(data: bytes):
    names = styles(data)
    paras = []
    cur = []
    style = None
    stack = []  # (skip, uc, up)
    up = False
    skip = False
    uc = 1
    pending_skip = 0
    star = False
    link = False
    for m in _TOKEN.finditer(data):
        word, num, hexc, sym, brace, nl, text = m.groups()
        if brace == b"{":
            stack.append((skip, uc, up))
            star = False
            continue
        if brace == b"}":
            if stack:
                skip, uc, up = stack.pop()
            star = False
            pending_skip = 0
            continue
        if nl is not None:
            continue
        if word is not None:
            if word in _SKIP_DEST or (star and word not in (b"fldrslt",)):
                if word == b"fldinst" and not skip:
                    link = True
                skip = True
                star = False
                continue
            star = False
            if skip:
                continue
            if word == b"par":
                paras.append((names.get(style, "" if style is None else "s%d" % style), "".join(cur), link))
                cur = []
                link = False
            elif word == b"pard":
                style = None
            elif word in (b"up", b"super"):
                up = (num is None or int(num) > 0)
            elif word in (b"nosupersub", b"plain"):
                up = False
            elif word == b"s":
                style = int(num)
            elif word == b"tab":
                cur.append("\t")
            elif word in (b"line",):
                cur.append("\n")
            elif word == b"cell":
                cur.append("\t\u2502\t")
            elif word == b"row":
                paras.append((names.get(style, "" if style is None else "s%d" % style) or "row", "".join(cur), link))
                cur = []
                link = False
            elif word == b"uc":
                uc = int(num)
            elif word == b"u":
                n = int(num)
                if n < 0:
                    n += 65536
                cur.append(chr(n))
                pending_skip = uc
            elif word == b"emdash":
                cur.append("\u2014")
            elif word == b"endash":
                cur.append("\u2013")
            elif word == b"lquote":
                cur.append("\u2018")
            elif word == b"rquote":
                cur.append("\u2019")
            elif word == b"ldblquote":
                cur.append("\u201c")
            elif word == b"rdblquote":
                cur.append("\u201d")
            elif word == b"bullet":
                cur.append("\u2022")
            elif word in (b"sect", b"page"):
                pass
            continue
        if hexc is not None:
            if pending_skip:
                pending_skip -= 1
                continue
            if not skip:
                cur.append(bytes([int(hexc, 16)]).decode("cp1252", "replace"))
            continue
        if sym is not None:
            if sym == b"*":
                star = True
                continue
            if skip:
                continue
            if sym in (b"\\", b"{", b"}"):
                cur.append(sym.decode())
            elif sym == b"~":
                cur.append("\u00a0")
            elif sym == b"-":
                pass
            elif sym == b"_":
                cur.append("\u2011")
            elif sym in (b"\n", b"\r"):
                paras.append((names.get(style, ""), "".join(cur), link))
                cur = []
                link = False
            continue
        if text is not None:
            if skip:
                continue
            s = text
            if pending_skip:
                k = min(pending_skip, len(s))
                s = s[k:]
                pending_skip -= k
            if s:
                if up and s.strip().isdigit():
                    continue   # a footnote marker (superscript number)
                cur.append(s.decode("cp1252", "replace"))
    if cur:
        paras.append((names.get(style, ""), "".join(cur), link))
    return paras


if __name__ == "__main__":
    import sys
    data = open(sys.argv[1], "rb").read()
    for st, tx, lk in paragraphs(data):
        print("%-28s%s|%s" % (st[:28], "L" if lk else " ", tx.replace("\t", " \u21e5 ")))
