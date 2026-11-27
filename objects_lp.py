#!/usr/bin/env python3
"""
Objects.LP - the level's object / enemy / trigger / path data for GoldenEye: Rogue Agent (DS).

Every level folder has one Objects.LP.  All numbers little endian.  Positions and distances
are 12.12 fixed point (level units, the same space as the .CVHM collision and Global.ROO
boxes), exactly like the model exports.

File layout
-----------
  u16 version = 6
  u16 numReserved                 (0, 1, 3 or 5 observed)
  u32 reservedIds[numReserved+1]  (mostly zeros; kept verbatim)

  Then three sections, back to back, and the file simply ends after the last record:
    A) "static" records   flags in {0, 2}  - the placed objects and trigger boxes
    B) "path"    records  flags == 5       - waypoint graphs used by the AI (patrol routes)
    C) "AI"      records  flags == 8       - the enemies (spawn points with a facing)

Record (flags 0 or 8), 44 bytes:
  u16 flags
  u16 size = 0x002c
  u32 w0                             (per-placement id, usually a slowly rising counter)
  u32 w1                             (object template hash; see TemplateIndex)
  s32 x, y, z                        (12.12 level coordinates)
  u16 pad = 0
  s16 angle                          (flags 8: 4096-steps-per-turn facing; flags 0: 0)
  u16 0xCCCC
  u16 h1                             (unique index; gaps are deleted slots)
  u32 sent                           (0xffffffff = none; else a reference to an h1 slot)
  u16 0xCCCC
  u16 typ                            (a 5-bit flags field, 0-31)
  u16 0x0000

Trigger box (flags 2), 68 bytes - the same layout as a record but `size` is 0x0044 and 24
extra bytes follow after the 14-byte tail:
  s32 x0, z0, height, x1, top, z1
          min = (P + (x0, 0, z0)), max = (P + (x1, height, z1))
          In practice x0 == -x1 and z0 == -z1 (the box is centred on P).

Path section (flags 5):
  u16 flags = 5
  u16 size
  u32 pathHash                       (rises by 1 per section; kept verbatim)
  u8  zeros[36]
  u16 numNodes
  u16 numEdges
  s32 node[numNodes][3]              (12.12 level coordinates)
  struct { u16 a; u16 b; s32 dist }  (dist = 12.12 node-to-node distance, both directions)

CodeStrings.CHR (referenced by h1)
----------------------------------
  u16 version = 1
  u16 count
  u16 stringSize                     (size of the strings region in bytes)
  u16 0xCCCC
  u32 zero, u32 zero
  u32 offsetEnd[count]               (cumulative end offset of every string, relative to
                                      the strings region)
  u8  strings[]                      (NUL terminated)

Templates.CRT + Global.LVL (w1)
-------------------------------
  w1 is the hash of the object template name.  Static (a=9) and AI (a=0x0b) templates live
  in Templates.CRT as 16-byte entries { u32 hash, u32 kind, u32 zero, u32 index }.  The
  names are stored right after the tables (NUL strings); the AI names are the Global.LVL
  "AI NEO AGENT" section, in the same order as the a=0x0b entries.
"""

import math
import re
import struct

LP_VERSION = 6
CHR_VERSION = 1
FIX = 4096
REC_STATIC = 0
REC_TRIGGER = 2
REC_PATH = 5
REC_AI = 8
RECORD_SIZE = 0x2c
TRIGGER_SIZE = 0x44


def _fixed_up(v):
    return int(round(v * FIX))


def _fixed_down(v):
    return v / FIX


class LPError(Exception):
    pass


# ---------------------------------------------------------------- CHR
class CodeStrings:
    """A level's CodeStrings.CHR: the names placed in that level, indexed by h1."""

    def __init__(self, data, path=''):
        if len(data) < 12:
            raise LPError('file is too small to be a CodeStrings.CHR')
        ver, count = struct.unpack_from('<HH', data, 0)
        if ver != CHR_VERSION:
            raise LPError('unsupported CodeStrings.CHR version %d' % ver)
        self.path = path
        self.version = ver
        self.string_size = struct.unpack_from('<H', data, 4)[0]
        self.marker = struct.unpack_from('<H', data, 6)[0] or 0xcccc
        base = 0x0c + 4 * count
        if base + self.string_size > len(data):
            raise LPError('CodeStrings.CHR strings region runs past the end of the file')
        self.base = base
        self.offsets = list(struct.unpack_from('<%dI' % count, data, 0x0c))
        strings = []
        for i, off in enumerate(self.offsets):
            if off < 0 or base + off >= len(data):
                raise LPError('CodeStrings.CHR has a bad string offset')
            end = data.find(b'\x00', base + off)
            if end < 0:
                raise LPError('CodeStrings.CHR string is not NUL terminated')
            strings.append(data[base + off:end].decode('latin-1'))
        last_end = base + self.offsets[-1] + len(strings[-1].encode('latin-1')) + 1 if self.offsets else base
        self.tail = data[last_end:base + self.string_size]
        self.strings = strings

    @property
    def count(self):
        return len(self.strings)

    def to_bytes(self):
        blob = b''.join(s.encode('latin-1') + b'\x00' for s in self.strings)
        offsets = []
        total = 0
        for s in self.strings:
            offsets.append(total)
            total += len(s.encode('latin-1')) + 1
        body = struct.pack('<HHHHI', self.version, len(self.strings),
                           len(blob) + len(self.tail), self.marker, 0)
        body += struct.pack('<%dI' % len(offsets), *offsets)
        body += blob + self.tail
        return body

    def append(self, names):
        """Append strings to the table; returns the h1 index of each new string."""
        first = self.count
        total = 0
        for s in self.strings:
            total += len(s.encode('latin-1')) + 1
        new_offsets = list(self.offsets)
        for name in names:
            enc = name.encode('latin-1') + b'\x00'
            new_offsets.append(total)
            total += len(enc)
        self.offsets = new_offsets
        self.strings.extend(names)
        self.string_size = total + len(self.tail)
        return list(range(first, first + len(names)))


def parse_chr(data, path=''):
    return CodeStrings(data, path)


def load_chr(path):
    return CodeStrings(open(path, 'rb').read(), path)


# ---------------------------------------------------------------- templates
def java31(name):
    """The game's 32-bit Java-style string hash (uppercase, h = h*31 + char)."""
    h = 0
    for c in str(name).upper():
        h = (h * 31 + ord(c)) & 0xffffffff
    return h


def _hash_to_name(names):
    """{java31(name.upper()): name} for a list of resource names."""
    out = {}
    for nm in names:
        out.setdefault(java31(nm), nm)
    return out


def _crt_entries(crt, lvl_ai_names=()):
    str_off = None
    m = re.search(rb'Barrel\x00', crt)
    if m:
        str_off = m.start()
    names = []
    if str_off is not None:
        for m in re.finditer(rb'[ -~]{3,}\x00', crt[str_off:]):
            names.append(m.group()[:-1].decode('latin-1'))
    a9, ab = [], []
    off = 0x60
    table_end = str_off if str_off is not None else len(crt)
    while off + 16 <= table_end:
        h, a, b = struct.unpack_from('<III', crt, off)
        # The third word is a flags field, not a "must be zero" marker: the
        # fourth object template (w1 0x8de900b2, "Destructable Bottle2") has
        # 0x00000808 there.  Filtering on b == 0 drops that real template and
        # shifts every following name by one slot.
        if a in (9, 0x0b):
            (a9 if a == 9 else ab).append((h, b))
        off += 16
    ai_by_hash = _hash_to_name(list(lvl_ai_names) + names)
    for i, (h, _b) in enumerate(a9):
        yield h, names[i] if i < len(names) else 'object template %08x' % h
    for i, h in enumerate(ab):
        # The AI NEO AGENT names are spawn names, not template names: matching
        # them positionally gives 0/95 hash matches.  Several templates are
        # named in Global.LVL Resource Templates (e.g. P90 Example) or in the
        # CRT string table (Goldfinger, Oddjob, ...), so resolve by hash first.
        nm = ai_by_hash.get(h)
        if nm is None and i < len(lvl_ai_names):
            nm = lvl_ai_names[i]
        yield h, nm or ('AI template %08x' % h)


def load_template_names(crt_path, lvl_path=None):
    """Return {w1: readable name} from Templates.CRT + Global.LVL (both optional)."""
    return {w1: name for w1, name, _kind in load_template_details(crt_path, lvl_path)}


def load_template_details(crt_path, lvl_path=None):
    """Return [{w1, name, kind}] from Templates.CRT (+ Global.LVL). kind is 'object' or 'ai'."""
    try:
        crt = open(crt_path, 'rb').read()
    except OSError:
        return []
    ai_names = ()
    if lvl_path:
        try:
            text = open(lvl_path, 'rb').read().decode('latin-1')
        except OSError:
            text = ''
        sections = re.split(r'(BEGIN "[^"]+")', text)
        cur = None
        body = {}
        for part in sections:
            if part.startswith('BEGIN'):
                cur = part[7:-1]
                continue
            if cur is not None:
                body.setdefault(cur, []).append(part)
        ai_names = re.findall(r'"([^"]+)"', ''.join(body.get('AI NEO AGENT', [])))
        all_lvl_names = [nm for parts in body.values()
                         for nm in re.findall(r'"([^"]+)"', ''.join(parts))]
    else:
        all_lvl_names = []
    str_off = None
    m = re.search(rb'Barrel\x00', crt)
    if m:
        str_off = m.start()
    names = []
    if str_off is not None:
        for m in re.finditer(rb'[ -~]{3,}\x00', crt[str_off:]):
            names.append(m.group()[:-1].decode('latin-1'))
    a9, ab = [], []
    off = 0x60
    table_end = str_off if str_off is not None else len(crt)
    while off + 16 <= table_end:
        h, a, b = struct.unpack_from('<III', crt, off)
        # See _crt_entries: b is a flags word.  The fourth object template has
        # b == 0x00000808; requiring b == 0 loses it and mislabels 70 templates.
        if a in (9, 0x0b):
            (a9 if a == 9 else ab).append(h)
        off += 16
    out = []
    for i, h in enumerate(a9):
        out.append(dict(w1=h, name=names[i] if i < len(names) else 'object template %08x' % h,
                        kind='object'))
    ai_by_hash = _hash_to_name(all_lvl_names + list(ai_names) + names)
    for i, h in enumerate(ab):
        # See _crt_entries: AI NEO AGENT is a spawn-name list (0/95 hash
        # matches); prefer a hash-verified template name.
        nm = ai_by_hash.get(h)
        if nm is None and i < len(ai_names):
            nm = ai_names[i]
        out.append(dict(w1=h, name=nm or ('AI template %08x' % h), kind='ai'))
    return out


# ---------------------------------------------------------------- LP
class LPRecord:
    def __init__(self, flags, size, w0, w1, pos, tail6, h1, sent, typ, extra6=None):
        self.flags = flags
        self.size = size
        self.w0 = w0
        self.w1 = w1
        self.pos = list(pos)          # 3 x s32, 12.12
        self.tail6 = bytes(tail6)     # bytes 24..29 of the record
        self.h1 = h1
        self.sent = sent
        self.typ = typ
        self.extra6 = bytes(extra6) if extra6 else None   # trigger boxes only (24 bytes)

    @property
    def position(self):
        return [_fixed_down(v) for v in self.pos]

    def set_position(self, x, y, z):
        self.pos = [_fixed_up(x), _fixed_up(y), _fixed_up(z)]

    @property
    def is_trigger(self):
        return self.flags == REC_TRIGGER

    @property
    def is_ai(self):
        return self.flags == REC_AI

    @property
    def angle(self):
        """AI facing, signed 16-bit angle (4096 steps per turn)."""
        return struct.unpack_from('<h', self.tail6, 2)[0]

    def set_angle(self, brads):
        t6 = bytearray(self.tail6)
        struct.pack_into('<h', t6, 2, max(-32768, min(32767, int(brads))))
        self.tail6 = bytes(t6)

    @property
    def box(self):
        """Trigger box (min, max) in level units, or None for non-triggers."""
        if not self.is_trigger or not self.extra6 or len(self.extra6) != 24:
            return None
        x0, z0, h, x1, top, z1 = struct.unpack_from('<6i', self.extra6, 0)
        p = self.pos
        return ((_fixed_down(p[0] + x0), _fixed_down(p[1]), _fixed_down(p[2] + z0)),
                (_fixed_down(p[0] + x1), _fixed_down(p[1] + h), _fixed_down(p[2] + z1)))

    def set_box(self, half_x, half_z, height):
        self.extra6 = struct.pack('<6i',
                                  -_fixed_up(half_x), 0, -_fixed_up(half_z),
                                  _fixed_up(half_x), _fixed_up(height), _fixed_up(half_z))

    def to_bytes(self):
        body = struct.pack('<HHIIiii', self.flags, self.size, self.w0, self.w1,
                           self.pos[0], self.pos[1], self.pos[2])
        body += self.tail6
        body += struct.pack('<HHIH', 0xcccc, self.h1, self.sent, 0xcccc)
        body += struct.pack('<HH', self.typ, 0)
        if self.is_trigger:
            body += self.extra6 or b'\x00' * 24
        return body


class LPPath:
    def __init__(self, path_hash, zeros, nodes, edges):
        self.hash = path_hash
        self.zeros = bytes(zeros)
        self.nodes = [list(n) for n in nodes]    # s32 x,y,z per node
        self.edges = [list(e) for e in edges]    # (a, b, dist s32)

    @property
    def node_count(self):
        return len(self.nodes)

    @property
    def edge_count(self):
        return len(self.edges)

    def node_pos(self, i):
        return [_fixed_down(v) for v in self.nodes[i]]

    def set_node_pos(self, i, x, y, z):
        self.nodes[i] = [_fixed_up(x), _fixed_up(y), _fixed_up(z)]

    def recompute_distances(self):
        for e in self.edges:
            a, b, _ = e
            ax, ay, az = self.nodes[a]
            bx, by, bz = self.nodes[b]
            e[2] = _fixed_up(math.dist((ax, ay, az), (bx, by, bz)))

    @property
    def size(self):
        return 48 + len(self.nodes) * 12 + len(self.edges) * 8

    def to_bytes(self):
        body = struct.pack('<HH', REC_PATH, self.size)
        body += struct.pack('<I', self.hash)
        body += self.zeros
        body += struct.pack('<HH', len(self.nodes), len(self.edges))
        for n in self.nodes:
            body += struct.pack('<3i', *n)
        for a, b, dist in self.edges:
            body += struct.pack('<HHi', a, b, dist)
        return body


class LPFile:
    def __init__(self, version, nn, reserved_ids, items, path=''):
        self.version = version
        self.nn = nn
        self.reserved_ids = reserved_ids
        self.items = items          # list of LPRecord / LPPath in file order
        self.path = path

    # queries -------------------------------------------------------
    def records(self, flags=None):
        return [it for it in self.items
                if isinstance(it, LPRecord) and (flags is None or it.flags == flags)]

    def paths(self):
        return [it for it in self.items if isinstance(it, LPPath)]

    def h1_used(self):
        return set(r.h1 for r in self.records())

    def free_h1(self):
        used = self.h1_used()
        i = 0
        while i in used:
            i += 1
        return i

    def max_h1(self):
        h1s = [r.h1 for r in self.records()]
        return max(h1s) if h1s else -1

    # mutation ------------------------------------------------------
    def add_record(self, record):
        if record.flags in (REC_STATIC, REC_TRIGGER):
            insert_at = len(self.items)
            for i, it in enumerate(self.items):
                if isinstance(it, LPPath) or (isinstance(it, LPRecord) and it.flags == REC_AI):
                    insert_at = i
                    break
            self.items.insert(insert_at, record)
        elif record.flags == REC_AI:
            self.items.append(record)
        else:
            raise LPError('cannot add a record with flags %d' % record.flags)

    def add_path(self, path):
        insert_at = len(self.items)
        for i, it in enumerate(self.items):
            if isinstance(it, LPRecord) and it.flags == REC_AI:
                insert_at = i
                break
        self.items.insert(insert_at, path)

    def remove_record(self, record):
        self.items.remove(record)

    def remove_path(self, path):
        self.items.remove(path)

    # serialization ---------------------------------------------------
    def to_bytes(self):
        body = struct.pack('<HH', self.version, self.nn)
        for v in self.reserved_ids:
            body += struct.pack('<I', v)
        for it in self.items:
            body += it.to_bytes()
        return body


def parse_bytes(data, path=''):
    if len(data) < 4:
        raise LPError('file is too small to be an Objects.LP')
    version, nn = struct.unpack_from('<HH', data, 0)
    if version != LP_VERSION:
        raise LPError('unsupported Objects.LP version %d' % version)
    off = 4
    if off + 4 * (nn + 1) > len(data):
        raise LPError('reserved-id table runs past the end of the file')
    reserved = list(struct.unpack_from('<%dI' % (nn + 1), data, off))
    off += 4 * (nn + 1)
    items = []
    while off + 4 <= len(data):
        flags, size = struct.unpack_from('<HH', data, off)
        if flags == REC_PATH:
            if off + size > len(data):
                raise LPError('path section at %#x overruns the file' % off)
            phash, = struct.unpack_from('<I', data, off + 4)
            zeros = data[off + 8:off + 44]
            nnodes, nedges = struct.unpack_from('<HH', data, off + 44)
            base = off + 48
            nodes = [list(struct.unpack_from('<3i', data, base + i * 12)) for i in range(nnodes)]
            edges = [list(struct.unpack_from('<HHi', data, base + nnodes * 12 + i * 8))
                     for i in range(nedges)]
            items.append(LPPath(phash, zeros, nodes, edges))
            off += size
            continue
        if size not in (RECORD_SIZE, TRIGGER_SIZE) or flags not in (REC_STATIC, REC_TRIGGER, REC_AI):
            raise LPError('bad record at %#x (flags=%d, size=%d)' % (off, flags, size))
        if off + size > len(data):
            raise LPError('record at %#x overruns the file' % off)
        rec = data[off:off + size]
        w0, w1 = struct.unpack_from('<II', rec, 4)
        pos = list(struct.unpack_from('<3i', rec, 12))
        tail6 = rec[24:30]
        cc1, h1 = struct.unpack_from('<HH', rec, 30)
        sent, = struct.unpack_from('<I', rec, 34)
        cc2, typ, tail0 = struct.unpack_from('<HHH', rec, 38)
        if cc1 != 0xcccc or cc2 != 0xcccc or tail0 != 0:
            raise LPError('record at %#x has a bad marker' % off)
        extra6 = rec[44:TRIGGER_SIZE] if flags == REC_TRIGGER else None
        items.append(LPRecord(flags, size, w0, w1, pos, tail6, h1, sent, typ, extra6))
        off += size
    if off != len(data):
        raise LPError('trailing data at %#x' % off)
    return LPFile(version, nn, reserved, items, path)


def parse(path):
    return parse_bytes(open(path, 'rb').read(), path)
