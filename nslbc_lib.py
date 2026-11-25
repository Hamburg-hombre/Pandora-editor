"""Shared helpers for the NSLBC level-script format.

The extracted ROM work folder defaults to `<this folder>/../work`; pass any
folder to `parse()`/`build_hashdict(work)` to use another one.
"""
import os
import re
import struct
import glob
import collections
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.abspath(os.path.join(HERE, '..', 'work'))
sys.path.insert(0, HERE)
from sound import java31


def sections(data):
    """Return section offsets/sizes: A/B/C/D tables, code blob, string table."""
    w = struct.unpack_from('<8I', data, 0)
    a = 0x20
    b = a + 16 * w[1]
    c = b + 16 * w[2]
    d = c + 16 * w[3]
    code = d + 4 * w[4]
    strings = code + w[5]
    return dict(a=a, b=b, c=c, d=d, code=code, str=strings, size=len(data), hdr=w)


def parse(data):
    s = sections(data)
    A = [struct.unpack_from('<4I', data, s['a'] + 16 * i) for i in range(s['hdr'][1])]
    B = [struct.unpack_from('<4I', data, s['b'] + 16 * i) for i in range(s['hdr'][2])]
    C = [struct.unpack_from('<4I', data, s['c'] + 16 * i) for i in range(s['hdr'][3])]
    D = [struct.unpack_from('<I', data, s['d'] + 4 * i)[0] for i in range(s['hdr'][4])]
    code = data[s['code']:s['str']]
    strtab = data[s['str']:]
    strs = []
    off = 0
    for part in strtab.split(b'\x00'):
        strs.append((s['str'] + off, part.decode('latin-1')))
        off += len(part) + 1
    return dict(s=s, A=A, B=B, C=C, D=D, code=code, strs=strs, raw=data)


def strings_from(data, start, minlen=3):
    for m in re.finditer(rb'[ -~]{%d,}\x00' % minlen, data[start:]):
        yield start + m.start(), m.group()[:-1].decode('latin-1')


def build_hashdict(work=None):
    """Hash -> labels for every name we can find in the extracted ROM."""
    work = work or WORK
    rev = collections.defaultdict(set)

    def add(name, label):
        if not name:
            return
        rev[java31(name)].add(label)
        rev[java31(name.upper())].add(label + '^')

    lvl = open(os.path.join(work, 'Global.LVL'), 'rb').read().decode('latin-1')
    for m in re.finditer(r'BEGIN "([^"]+)"(.*?)END', lvl, re.S):
        sec = m.group(1)
        for _i, nm in re.findall(r'(\d+)\s+"([^"]*)"', m.group(2)):
            add(nm, sec)
    crt = open(os.path.join(work, 'Templates.CRT'), 'rb').read()
    for m in re.finditer(rb'[ -~]{3,}\x00', crt):
        add(m.group()[:-1].decode('latin-1'), 'CRTstr')
    # template hash tables (kind 9 / kind 11 entries)
    for i in range(200):
        for off in (0xb60 + 16 * i, 0x6c0 + 16 * i):
            if off + 4 <= len(crt):
                h = struct.unpack_from('<I', crt, off)[0]
                if h:
                    rev[h].add('CRTtab@%x' % off)
    for p in glob.glob(os.path.join(work, 'Levels', '*', '*.CHR')):
        d = open(p, 'rb').read()
        for m in re.finditer(rb'[ -~]{3,}\x00', d):
            add(m.group()[:-1].decode('latin-1'), 'CHR:' + os.path.basename(os.path.dirname(p)))
    for p in glob.glob(os.path.join(work, 'Levels', '*', '*.[Nn][Ss][Ll][Bb][Cc]')):
        try:
            r = parse(open(p, 'rb').read())
        except Exception:
            continue
        for _o, txt in r['strs']:
            add(txt, 'STR')
    return rev


def resolve(rev, h):
    labs = rev.get(h, set())
    if not labs:
        return ''
    good = [x for x in labs if x != 'STR' and not x.startswith('CRTtab')]
    return ','.join(sorted(good)[:4] if good else sorted(labs)[:3])
