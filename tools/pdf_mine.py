#!/usr/bin/env python3
"""tools/pdf_mine.py -- crude PDF text miner (no external deps).

Why it exists: the vendor documentation (正点原子 达芬奇Pro 开发指南, schematics) is
the only place that may name the flash part / the part name their flow uses.
pypdf is NOT installed in this environment, so we do it by hand:
  * collect every `stream ... endstream` block
  * inflate the FlateDecode ones with zlib
  * pull the text-showing operators out of the content stream
    ( Tj, TJ, ' , "  -- literal (...) and hex <...> strings )
  * also recover ToUnicode CMaps so CID-encoded Chinese can be mapped back
Then dump one .txt per PDF and grep that.

Usage: python tools/pdf_mine.py <pdf> [<pdf> ...]
Writes <pdf>.mined.txt next to the input.
"""
import re
import sys
import zlib
import os


def iter_streams(data: bytes):
    for m in re.finditer(rb"stream\r?\n", data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end == -1:
            continue
        yield data[start:end]


def inflate(blob: bytes):
    for trim in (0, 1, 2):
        b = blob[: len(blob) - trim] if trim else blob
        try:
            return zlib.decompress(b)
        except Exception:
            continue
    try:
        return zlib.decompressobj().decompress(blob)
    except Exception:
        return None


def unescape(s: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):  # backslash
            n = s[i + 1]
            mp = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}
            if n in mp:
                out.append(mp[n]); i += 2; continue
            if 0x30 <= n <= 0x37:
                j = i + 1
                oct_s = b""
                while j < len(s) and len(oct_s) < 3 and 0x30 <= s[j] <= 0x37:
                    oct_s += s[j:j + 1]; j += 1
                out.append(int(oct_s, 8) & 0xFF); i = j; continue
            out.append(n); i += 2; continue
        out.append(c); i += 1
    return bytes(out)


TOKEN = re.compile(rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]*>", re.S)


def strings_from_content(content: bytes):
    """Return list of raw byte strings shown by text operators."""
    res = []
    for m in TOKEN.finditer(content):
        tok = m.group(0)
        # only keep it if a text-showing operator follows reasonably soon
        tail = content[m.end():m.end() + 24]
        if not re.match(rb"\s*(Tj|TJ|'|\")", tail) and not re.match(rb"\s*\]\s*TJ", tail):
            # inside a TJ array the token is followed by more tokens/']'
            if not re.match(rb"[\s\d\.\-\]<\(\[]", tail):
                continue
        if tok.startswith(b"("):
            res.append(unescape(tok[1:-1]))
        else:
            h = re.sub(rb"\s", b"", tok[1:-1])
            if len(h) % 2:
                h += b"0"
            try:
                res.append(bytes.fromhex(h.decode("ascii")))
            except Exception:
                pass
    return res


def main(paths):
    for path in paths:
        with open(path, "rb") as f:
            data = f.read()
        chunks = []
        raw_ascii = []
        for blob in iter_streams(data):
            dec = inflate(blob)
            if dec is None:
                # uncompressed content stream?
                dec = blob if b"Tj" in blob or b"TJ" in blob else None
            if dec is None:
                continue
            chunks.append(dec)
            for s in strings_from_content(dec):
                if len(s) >= 2:
                    raw_ascii.append(s.decode("latin-1"))
        out = path + ".mined.txt"
        with open(out, "w", encoding="utf-8", errors="replace") as f:
            f.write("### FILE: %s (%d bytes, %d streams)\n" % (path, len(data), len(chunks)))
            f.write("\n".join(raw_ascii))
        print("%s -> %s  (%d text runs)" % (path, out, len(raw_ascii)))


if __name__ == "__main__":
    main(sys.argv[1:])
