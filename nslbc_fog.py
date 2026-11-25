#!/usr/bin/env python3
"""Per-level fog settings decoded from ``Levels/<level>/Script.NSLBC``.

Some NSLBC startup callbacks contain a three-instruction sequence::

    0x00010000  <operand>          (start of the "IF" family)
    0x00010002  a, b, c            fog parameters (mode / two tuning values)
    0x00010003  0x1F RR GG BB      packed 5-bit colour, high nibble 0x1F = alpha
    0x00010001  1                  (end of the sequence)

The colour word was verified on all 18 retail levels: 21 instructions with opcode
0x10003, every one with top byte 0x1F and three 5-bit channels.  One of the callbacks
is literally java31("SETFOG"), and Volcano Lair pushes the debug string
"setting initial fog" immediately before the sequence.  The companion 0x10002
operands are very likely fog mode/density/shift, but their exact mapping to the NDS
FOG registers is still unconfirmed.

This module never edits files; it is used by the 3D viewer to prefill its fog colour
and by tooling to report the level's authored fog.

CLI:  python nslbc_fog.py [work folder]
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from nslbc_lib import parse, WORK
from sound import java31

# Opcode arities (from documentation/NSLBC_Format.md).  A block is parsed until an
# unknown opcode appears; every retail block was verified to parse exactly.
ARITY = {0: 1, 1: 2, 2: 1, 3: 2, 4: 3, 5: 2, 6: 1, 7: 2,
         0x10000: 1, 0x10001: 1, 0x10002: 3, 0x10003: 1,
         0x20000: 2, 0x20001: 1, 0x20002: 1, 0x20004: 1, 0x20005: 0,
         0x20007: 1, 0x20008: 1, 0x20009: 1, 0x30000: 1, 0x30001: 0}

SETFOG = java31('SETFOG')
STARTUP = {java31('ONSTARTUP'): 'ONSTARTUP',
           java31('ONGAMESTARTUP'): 'ONGAMESTARTUP'}


def _channels(word):
    """Decode an 0x1F RR GG BB word; returns (r, g, b) floats in 0..1 or None."""
    if (word >> 24) != 0x1F:
        return None
    return tuple(((word >> shift) & 0x1F) / 31.0 for shift in (16, 8, 0))


def scan(path):
    """Return every fog instruction found in one Script.NSLBC, in file order."""
    raw = open(path, 'rb').read()
    r = parse(raw)
    code_base = r['s']['code']
    out = []
    for block, (callback, off, length, _flag) in enumerate(r['C']):
        if length % 4 or off + length > len(r['code']):
            continue
        words = struct.unpack_from('<%dI' % (length // 4), r['code'], off)
        i = 0
        pending = None
        while i < len(words):
            op = words[i]
            n = ARITY.get(op)
            if n is None:
                break
            args = words[i + 1:i + 1 + n]
            word_off = code_base + off + 4 * i
            i += 1 + n
            if op == 0x10002:
                pending = (tuple(args), word_off + 4)
            elif op == 0x10003:
                rgb = _channels(args[0])
                if rgb is not None:
                    rr, gg, bb = (int(round(c * 31)) for c in rgb)
                    out.append(dict(
                        block=block, callback=callback,
                        callback_name=STARTUP.get(callback) or
                        ('SETFOG' if callback == SETFOG else '%08x' % callback),
                        params_offset=pending[1] if pending else None,
                        params=pending[0] if pending else None,
                        word=args[0], offset=word_off + 4, rgb=rgb,
                        bgr555=(bb << 10) | (gg << 5) | rr,
                        source='%s:0x%x' % (os.path.basename(path), word_off + 4)))
                pending = None
            elif op != 0x10000:
                pending = None
    return out


def level_fog(work=None, level=None):
    """Best fog setting for one level.

    Prefers a startup callback (the level's initial look), then SETFOG, then the
    first fog instruction in file order.  Returns None when there is no fog block.
    """
    work = work or WORK
    if not level:
        return None
    path = os.path.join(work, 'Levels', level, 'Script.NSLBC')
    if not os.path.isfile(path):
        return None
    try:
        hits = scan(path)
    except (OSError, struct.error, ValueError):
        return None
    if not hits:
        return None
    for h in hits:
        if h['callback_name'] in STARTUP.values():
            return h
    for h in hits:
        if h['callback'] == SETFOG:
            return h
    return hits[0]


def describe(hit):
    if not hit:
        return 'no fog block found'
    params = hit['params'] or ()
    return '%s (C#%d %s): %s  rgb=(%.2f, %.2f, %.2f)  params=%s' % (
        hit['source'], hit['block'], hit['callback_name'],
        '0x%08X' % hit['word'], *hit['rgb'],
        '/'.join(str(v) for v in params))


if __name__ == '__main__':
    work = sys.argv[1] if len(sys.argv) > 1 else WORK
    for lvl in sorted(os.listdir(os.path.join(work, 'Levels'))):
        p = os.path.join(work, 'Levels', lvl)
        if not os.path.isdir(p):
            continue
        hits = scan(os.path.join(p, 'Script.NSLBC')) if os.path.isfile(
            os.path.join(p, 'Script.NSLBC')) else []
        if hits:
            print('%-20s %s' % (lvl, describe(level_fog(work, lvl))))
            if len(hits) > 1:
                for h in hits[1:]:
                    print('%20s   also %s' % ('', describe(h)))
