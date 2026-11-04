#!/usr/bin/env python3
"""tools/pdf_text.py -- real PDF text extraction for CID/Identity-H documents.

WHY: tools/pdf_mine.py only pulled raw Tj/TJ byte strings. For this document
those bytes are 2-byte *glyph* IDs (subset CID fonts), so it produced 61 MB of
output with ZERO ASCII hits. To get real text you must resolve each glyph ID
through the font's /ToUnicode CMap.

WHAT IT DOES
  1. scans `N G obj ... endobj`, inflating FlateDecode streams; also unpacks
     object streams (/Type /ObjStm) so fonts/pages buried in them are found
  2. walks /Root -> /Pages -> /Kids to get the real page order
  3. per page: reads /Resources /Font (name -> font object) and /Contents,
     and for every font parses its /ToUnicode CMap (beginbfchar/beginbfrange)
  4. decodes the content stream, tracking `/Fx ... Tf` to pick the right map,
     and emits UTF-8 text per page

Usage: python tools/pdf_text.py <pdf> <outdir>
"""
import os
import re
import sys
import zlib

OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", re.S)


def inflate(b):
    for trim in (0, 1, 2):
        try:
            return zlib.decompress(b[:len(b) - trim] if trim else b)
        except Exception:
            pass
    try:
        d = zlib.decompressobj()
        return d.decompress(b)
    except Exception:
        return None


def get_stream(body):
    m = re.search(rb"stream\r?\n", body)
    if not m:
        return None, body
    dic = body[:m.start()]
    end = body.find(b"endstream", m.end())
    raw = body[m.end():end if end != -1 else len(body)]
    if b"FlateDecode" in dic:
        dec = inflate(raw)
        if dec is None:
            return None, dic
        return dec, dic
    return raw, dic


class Doc:
    def __init__(self, data):
        self.objs = {}      # num -> (dict_bytes, stream_bytes_or_None)
        for m in OBJ_RE.finditer(data):
            num, body = int(m.group(1)), m.group(3)
            stream, dic = get_stream(body)
            self.objs[num] = (dic, stream if stream is not None else None)
            if stream is None and b"/ObjStm" in dic:
                pass
        # unpack object streams
        for num, (dic, st) in list(self.objs.items()):
            if st is None or b"/ObjStm" not in dic:
                continue
            n = int(re.search(rb"/N\s+(\d+)", dic).group(1))
            first = int(re.search(rb"/First\s+(\d+)", dic).group(1))
            hdr = st[:first].split()
            for i in range(n):
                onum = int(hdr[2 * i]); off = int(hdr[2 * i + 1])
                nxt = int(hdr[2 * i + 3]) if 2 * i + 3 < len(hdr) else len(st) - first
                sub = st[first + off: first + nxt]
                s2, d2 = get_stream(sub)
                head = sub.split(b"stream")[0]
                self.objs.setdefault(onum, (head, s2))

    def ref(self, blob, key):
        m = re.search(key.encode() + rb"\s+(\d+)\s+\d+\s+R", blob)
        return int(m.group(1)) if m else None

    def to_unicode(self, fobj):
        """Parse the font's /ToUnicode CMap -> {code: str}."""
        dic, st = self.objs.get(fobj, (b"", None))
        t = self.ref(dic, "/ToUnicode")
        if t is None or t not in self.objs:
            return None
        _, cm = self.objs[t]
        if cm is None:
            return None
        m = {}
        for blk in re.findall(rb"beginbfchar(.*?)endbfchar", cm, re.S):
            for a, b in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
                m[int(a, 16)] = bytes.fromhex(b.decode()).decode("utf-16-be", "replace")
        for blk in re.findall(rb"beginbfrange(.*?)endbfrange", cm, re.S):
            for a, b, c in re.findall(
                    rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
                lo, hi = int(a, 16), int(b, 16)
                base = int(c, 16)
                if hi < lo or hi - lo > 65535:
                    continue
                for k in range(lo, hi + 1):
                    try:
                        m[k] = chr(base + (k - lo))
                    except (ValueError, OverflowError):
                        pass
        return m

    def pages(self):
        root = None
        for num, (dic, _) in self.objs.items():
            if b"/Type" in dic and re.search(rb"/Type\s*/Catalog", dic):
                root = num
        kids = []
        if root is not None:
            pt = self.ref(self.objs[root][0], "/Pages")
            self._walk(pt, kids, set())
        if not kids:
            for num, (dic, _) in self.objs.items():
                if re.search(rb"/Type\s*/Page\b", dic):
                    kids.append(num)
        return kids

    def _walk(self, num, out, seen):
        if num is None or num in seen or num not in self.objs:
            return
        seen.add(num)
        dic, _ = self.objs[num]
        if re.search(rb"/Type\s*/Page\b", dic) and not re.search(rb"/Type\s*/Pages", dic):
            out.append(num)
            return
        km = re.search(rb"/Kids\s*\[(.*?)\]", dic, re.S)
        if km:
            for r in re.findall(rb"(\d+)\s+\d+\s+R", km.group(1)):
                self._walk(int(r), out, seen)


def decode(content):
    """Tokenise into ('t', fontname) / ('s', literal bytes) / ('h', hex bytes).

    Returns (tokens, None). Font switching is done by the caller from the
    ('t', name) tokens -- an earlier version tried to detect `Tf` from the
    already-split token list, which never matched (names come out one byte per
    token), so every string was decoded with the wrong font's CMap and the
    whole document came out as mojibake.
    """
    out = []
    i = 0
    n = len(content)
    while i < n:
        c = content[i:i + 1]
        if c == b"(":
            j = i + 1
            depth = 1
            buf = bytearray()
            while j < n and depth:
                ch = content[j:j + 1]
                if ch == b"\\":
                    buf += content[j:j + 2]
                    j += 2
                    continue
                if ch == b"(":
                    depth += 1
                elif ch == b")":
                    depth -= 1
                    if depth == 0:
                        break
                buf += ch
                j += 1
            out.append(("s", bytes(buf)))
            i = j + 1
        elif c == b"<" and content[i:i + 2] != b"<<":
            j = content.find(b">", i)
            if j == -1:
                break
            out.append(("h", re.sub(rb"\s", b"", content[i + 1:j])))
            i = j + 1
        else:
            m = re.match(rb"/([^\s/\[\]<>()]+)\s+[-\d.]+\s+Tf", content[i:i + 96])
            if m:
                out.append(("t", m.group(1).decode("latin-1")))
                i += m.end()
                continue
            i += 1
    return out


UNESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b",
         b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def main(pdf, outdir):
    data = open(pdf, "rb").read()
    doc = Doc(data)
    os.makedirs(outdir, exist_ok=True)
    pages = doc.pages()
    sys.stderr.write("objects=%d pages=%d\n" % (len(doc.objs), len(pages)))
    alltxt = []
    for idx, pnum in enumerate(pages):
        dic, _ = doc.objs.get(pnum, (b"", None))
        fonts = {}
        fm = re.search(rb"/Font\s*<<(.*?)>>", dic, re.S)
        if not fm:
            r = doc.ref(dic, "/Resources")
            if r and r in doc.objs:
                fm = re.search(rb"/Font\s*<<(.*?)>>", doc.objs[r][0], re.S)
        if fm:
            for name, onum in re.findall(rb"/([^\s/\[\]<>()]+)\s+(\d+)\s+\d+\s+R", fm.group(1)):
                onum = int(onum)
                fdict = doc.objs.get(onum, (b"", None))[0]
                two = bool(re.search(rb"/Subtype\s*/Type0", fdict))
                fonts[name.decode("latin-1")] = (doc.to_unicode(onum), two)
        blob = b""
        cm = re.search(rb"/Contents\s+(\d+)\s+\d+\s+R", dic)
        if cm and int(cm.group(1)) in doc.objs:
            blob = doc.objs[int(cm.group(1))][1] or b""
        else:
            cm2 = re.search(rb"/Contents\s*\[(.*?)\]", dic, re.S)
            if cm2:
                for r in re.findall(rb"(\d+)\s+\d+\s+R", cm2.group(1)):
                    if int(r) in doc.objs and doc.objs[int(r)][1]:
                        blob += doc.objs[int(r)][1]
        if not blob:
            continue
        pieces = []
        cur = None
        two = True
        for kind, val in decode(blob):
            if kind == "t":
                mp = fonts.get(val)
                if mp:
                    cur, two = mp
                continue
            raw = val
            if kind == "s":
                raw = re.sub(rb"\\([nrtbf()\\])", lambda m: UNESC[m.group(1)], val)
            if cur is None:
                pieces.append(raw.decode("latin-1"))
                continue
            if kind == "h":
                try:
                    raw = bytes.fromhex(raw.decode("ascii"))
                except Exception:
                    continue
                two = True
            if two and len(raw) % 2 == 0:
                pieces.append("".join(
                    cur.get(raw[k] * 256 + raw[k + 1], "") for k in range(0, len(raw), 2)))
            else:
                pieces.append("".join(cur.get(b, "") for b in raw))
        txt = "".join(pieces)
        if txt.strip():
            alltxt.append("=== PAGE %d (obj %d) ===\n%s" % (idx + 1, pnum, txt))
    out = os.path.join(outdir, "guide_text.txt")
    with open(out, "w", encoding="utf-8", errors="replace") as f:
        f.write("\n".join(alltxt))
    print("pages with text: %d -> %s" % (len(alltxt), out))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
