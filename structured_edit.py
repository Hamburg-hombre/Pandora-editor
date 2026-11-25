#!/usr/bin/env python3
"""Structured rows and safe field edits for the decoded Pandora file types.

`build_rows(path, data)` returns (columns, rows).  Each row is a dict with
`values` (strings for the Treeview), `kind`, `off` (absolute byte offset in the
file), `scale`, `size`, `text` (current value), and a `hint` shown in the GUI.

`apply_edit(path, data, row, text)` returns the new file bytes.  Only fields
whose exact byte encoding is known are editable; everything else is view only.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nitro_anim as anim
import nslbc_lib
import nslbc_dis
import menu_tex
from sound import java31

MAX_ROWS = 400


def _row(values, kind, off, text, scale=None, size=None, hint='', extra=None):
    r = dict(values=tuple(str(v) for v in values), kind=kind, off=off,
             text=str(text), scale=scale, size=size, hint=hint)
    if extra:
        r.update(extra)
    return r


def _fix32(v):
    v &= 0xffffffff
    return v - 0x100000000 if v & 0x80000000 else v


def _fix16(v):
    v &= 0xffff
    return v - 0x10000 if v & 0x8000 else v


def _fmt_num(v, scale):
    return ('%.4f' % (v / float(scale))) if scale else str(v)


# ------------------------------------------------------------------ credits
def _credits(data):
    n = struct.unpack_from('<I', data, 0)[0]
    base = 4 + 4 * n
    offs = list(struct.unpack_from('<%dI' % n, data, 4))
    cols = ('#', 'string', 'byte offset')
    rows = []
    for i, o in enumerate(offs):
        s = base + o
        if s >= len(data):
            break
        e = data.find(b'\x00', s)
        if e < 0:
            break
        rows.append(_row((i, data[s:e].decode('latin-1'), '0x%X' % s),
                         'credits_str', s, data[s:e].decode('latin-1'),
                         hint='Any length; the offset table is rebuilt on save.',
                         extra=dict(index=i)))
    return cols, rows[:MAX_ROWS]


# ------------------------------------------------------------------ NSLBC
def _nslbc(path, data):
    r = nslbc_lib.parse(data)
    s = r['s']
    cols = ('section', 'what', 'value', 'byte offset')
    rows = []
    # strings (exact-length replacement only: instruction operands are offsets)
    for o, txt in r['strs']:
        if o + len(txt.encode('latin-1')) + 1 > len(data):
            continue
        rows.append(_row(('string', 'text', txt, '0x%X' % o), 'str_exact', o, txt,
                         hint='Same byte length only (string offsets are referenced by opcodes).',
                         extra=dict(maxlen=len(txt.encode('latin-1')))))
        if len(rows) >= MAX_ROWS:
            break
    # timer / delay operands (u32 milliseconds)
    for ci, (ch, off, ln, fl) in enumerate(r['C']):
        if ln % 4 or off + ln > len(r['code']):
            continue
        w = struct.unpack_from('<%dI' % (ln // 4), r['code'], off)
        i = 0
        while i < len(w):
            op = w[i]
            n = nslbc_dis.ARITY.get(op)
            if n is None or i + 1 + n > len(w):
                break
            args = w[i + 1:i + 1 + n]
            if op in (4, 7) and n >= 2:                 # TIMER / UNK7 delay
                ms = args[-1]
                addr = s['code'] + off + 4 * (i + n)
                cb = nslbc_dis.HASHNAMES.get(ch, '%08X' % ch)
                rows.append(_row(('C#%d' % ci, 'delay ms (%s)' % nslbc_dis.OPNAME[op],
                                  ms, '0x%X' % addr), 'u32', addr, ms,
                                 hint='u32 milliseconds (little endian).', extra=dict(size=4)))
            i += 1 + n
        if len(rows) >= MAX_ROWS:
            break
    return cols, rows[:MAX_ROWS]


def _curve_rows(rows, c, prefix, jac, scale_default):
    """Add editable rows for one NSBCA curve."""
    if c['const']:
        if c['kind'] == 'rot':
            return
        rows.append(_row((prefix, c['kind'] + ' const', _fmt_num(c['value'], 4096),
                          '0x%X' % c['off']), 'fx32', c['off'], c['value'],
                         scale=4096, size=4,
                         hint='Signed 1.19.12 (value / 4096).',
                         extra=dict(scale=4096, pair=(c['off'] + 4) if c['kind'] == 'scale' else None)))
        return
    for i, s in enumerate(c.get('samples', [])):
        if len(rows) >= MAX_ROWS:
            break
        if 'value' not in s:
            continue
        off = c['samples_at'] + i * (8 if c['samp_size'] == 8 else c['samp_size'])
        scale = 4096
        rows.append(_row((prefix, '%s key %d' % (c['kind'], i), _fmt_num(s['value'], scale),
                          '0x%X' % off), 'fx16' if c['samp_size'] <= 4 else 'fx32',
                         off, s['value'], scale=scale, size=2 if c['samp_size'] <= 4 else 4,
                         hint='Signed fixed point, value / 4096.'))
    return


def _anim(path, data):
    ext = os.path.splitext(path)[1].lower()
    parser = {'.nsbca': anim.parse_nsbca, '.nsbma': anim.parse_nsbma,
              '.nsbta': anim.parse_nsbta, '.nsbtp': anim.parse_nsbtp}[ext]
    r = parser(data)
    cols = ('track', 'channel', 'value', 'byte offset')
    rows = []
    for a in r['anims']:
        if ext == '.nsbca':
            for ti, t in enumerate(a['tracks']):
                for nm, c in t['channels']:
                    _curve_rows(rows, c, 'track%d node%d' % (ti, t['target']), a['off'], 4096)
        elif ext == '.nsbta':
            for t in a['tracks']:
                names = ('scale U', 'scale V', 'rotation', 'translate U', 'translate V')
                for ci, c in enumerate(t['channels']):
                    if c['flags'] == 0x1000:
                        for i, v in enumerate(c.get('samples', [])):
                            off = a['off'] + c['field'] + 2 * i
                            rows.append(_row((t['name'], '%s key %d' % (names[ci], i),
                                              _fmt_num(v, 4096), '0x%X' % off),
                                             'fx16', off, v, scale=4096, size=2,
                                             hint='Signed 1.3.12 UV value / 4096.'))
                    elif c['flags'] == 0x3000:
                        # field is the value itself; its raw u32 lives at ch.off+4
                        off = c['off'] + 4
                        raw = struct.unpack_from('<I', data, off)[0]
                        rows.append(_row((t['name'], names[ci] + ' fixed',
                                          _fmt_num(_fix32(raw), 4096), '0x%X' % off),
                                         'fx32', off, _fix32(raw), scale=4096, size=4,
                                         hint='Signed 1.19.12 / 4096.'))
                    elif c['flags'] == 0x2000:
                        rows.append(_row((t['name'], names[ci] + ' fixed rot',
                                          '0x%08X' % c['field'], '0x%X' % (c['off'] + 4)),
                                         'none', c['off'] + 4, '',
                                         hint='Fixed cos/sin as two u16 1.3.12 values.'))
        elif ext == '.nsbtp':
            for t in a['tracks']:
                for i, k in enumerate(t['keys']):
                    off = a['off'] + t['key_off'] + 4 * i
                    rows.append(_row((t['name'], 'frame %d' % k['frame'],
                                      'tex %d / pal %d' % (k['tex'], k['pal']), '0x%X' % off),
                                     'key3', off, k['frame'], size=4,
                                     hint='frame(u16), texture(u8), palette(u8)',
                                     extra=dict(tex=k['tex'], pal=k['pal'])))
        elif ext == '.nsbma':
            for t in a['tracks']:
                for ci, c in enumerate(t['channels']):
                    if c['flags'] & 0x20:
                        rows.append(_row((t['name'], 'ch%d fixed' % ci,
                                          '0x%04X' % c['fixed'], '0x%X' % c['at']),
                                         'u16hex', c['at'], c['fixed'], size=2,
                                         hint='Raw 16-bit material parameter (hex).'))
                    else:
                        for j, v in enumerate(c.get('values', [])):
                            off = c['at'] + 2 * j
                            rows.append(_row((t['name'], 'ch%d key %d' % (ci, j),
                                              '0x%04X' % v, '0x%X' % off),
                                             'u16hex', off, v, size=2,
                                             hint='Raw 16-bit packed keyframe (hex).'))
        if len(rows) >= MAX_ROWS:
            break
    return cols, rows[:MAX_ROWS]


def _tex(path, data):
    cols = ('#', 'offset', 'raw bytes', 'guessed size')
    rows = []
    for b in menu_tex.blocks(path):
        img = menu_tex.parse_image(b['payload'], b.get('rec'))
        w, h = menu_tex.guess_width(len(img[1]), b.get('rec')) if img else (None, None)
        rows.append(_row((b['index'], '0x%X' % b['offset'], b['usize'],
                          '%dx%d' % (w, h) if w else '?'), 'none', b['offset'], '',
                         hint='View-only: menu.bin stores absolute offsets; use Export PNGs.'))
    return cols, rows[:MAX_ROWS]


def build_rows(path, data):
    low = path.lower()
    try:
        if os.path.basename(low) == 'credits.bin':
            return _credits(data)
        if low.endswith('.nslbc'):
            return _nslbc(path, data)
        if low.endswith(('.nsbca', '.nsbma', '.nsbta', '.nsbtp')):
            return _anim(path, data)
        if low.endswith('.tex'):
            return _tex(path, data)
    except Exception as e:
        return ('status',), [_row(('Could not parse: %s: %s' % (type(e).__name__, e),),
                                  'none', 0, '', hint=str(e))]
    return None, []


# ------------------------------------------------------------------ apply
def apply_edit(path, data, row, text):
    """Return new bytes with `row` set to `text`; raise ValueError on bad input."""
    kind = row['kind']
    if kind == 'none':
        raise ValueError('this field is view-only')
    off = row['off']
    text = text.strip()
    if kind in ('credits_str', 'str_exact'):
        enc = text.encode('latin-1')
        if b'\x00' in enc:
            raise ValueError('NUL is not allowed')
        if kind == 'credits_str':
            n = struct.unpack_from('<I', data, 0)[0]
            base = 4 + 4 * n
            strings = []
            for o in struct.unpack_from('<%dI' % n, data, 4):
                s = base + o
                e = data.find(b'\x00', s)
                strings.append(data[s:e])
            strings[row['index']] = enc
            blob = b''.join(s + b'\x00' for s in strings)
            return (struct.pack('<I', n) +
                    struct.pack('<%dI' % n, *[sum(len(x) + 1 for x in strings[:i])
                                              for i in range(n)]) + blob)
        if len(enc) != row['maxlen']:
            raise ValueError('exactly %d byte(s) required (got %d)' % (row['maxlen'], len(enc)))
        out = bytearray(data)
        out[off:off + len(enc)] = enc
        return bytes(out)
    if kind in ('u32', 'u16hex', 'key3'):
        if kind == 'key3':
            parts = text.replace(',', ' ').split()
            if len(parts) != 3:
                raise ValueError('enter: frame texture palette')
            vals = [int(x, 0) for x in parts]
            out = bytearray(data)
            struct.pack_into('<HBB', out, off, vals[0] & 0xffff, vals[1] & 0xff, vals[2] & 0xff)
            return bytes(out)
        v = int(text, 0)
        out = bytearray(data)
        if kind == 'u32':
            struct.pack_into('<I', out, off, v & 0xffffffff)
        else:
            struct.pack_into('<H', out, off, v & 0xffff)
        return bytes(out)
    if kind in ('fx32', 'fx16'):
        v = float(text)
        scale = row['scale'] or 4096
        q = int(round(v * scale))
        out = bytearray(data)
        if kind == 'fx32':
            if not -0x80000000 <= q < 0x80000000:
                raise ValueError('value out of 32-bit range')
            struct.pack_into('<I', out, off, q & 0xffffffff)
            pair = row.get('pair')
            if pair is not None:
                # NSBCA stores scale together with its reciprocal (1/x).
                if abs(v) < 1e-9:
                    inv = 0
                else:
                    inv = int(round(scale / v))
                struct.pack_into('<I', out, pair, inv & 0xffffffff)
        else:
            if not -0x8000 <= q < 0x8000:
                raise ValueError('value out of 16-bit range')
            struct.pack_into('<H', out, off, q & 0xffff)
        return bytes(out)
    raise ValueError('unknown field kind %r' % kind)
