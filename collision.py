#!/usr/bin/env python3
"""GoldenEye: Rogue Agent (DS)  .CVHM collision files: read, export, generate.

Format (little endian)
----------------------
Header v1 (0x28 bytes):
    0x00  u32 magic 0xCCCC0104
    0x04  16 zero bytes
    0x14  u16 numVerts, u16 numTris, u16 numBvhNodes, u16 1
    0x1C  12 zero bytes
Header v2 (0x3C bytes): the same, then a second count block at 0x28 and 12 zero bytes.
    v2 files contain two meshes (low detail "T_*" then detailed "H_*").

Mesh data (repeated per mesh):
    numVerts * 12 bytes   3 x i32, position * 4096 (12.12 fixed point)
    numTris  * 8 bytes    3 x u16 vertex indices, u16 flags
    numBvhNodes * 32      u32 child0, u32 child1, i32 centre[3], i32 halfExtent[3]

BVH:
    child value odd  -> triangle (value >> 5)
    child value even -> internal node (value >> 1);  root is node 0
    box = centre +/- halfExtent;  numBvhNodes is always numTris - 1.

Trailer v1: u32 name hash, u16 0, name + NUL
Trailer v2: u32 hash1, u32 hash2, u16 0, u16 len(name1), name1 + NUL, name2 + NUL
    hash = Java String.hashCode: h = 0; for c: h = (h * 31 + c) & 0xFFFFFFFF

Coordinates are the level's world space (not the .nsbmd model's local space), in the same
units as the render models.  The GUI uses that to align generated collision to the old one.

Level room boxes (Global.ROO)
-----------------------------
The game only tests collision inside the room the player is in, and each room's extent is
stored in the level's Global.ROO.  Growing a .CVHM past that box makes the new part
unreachable, so generate_from_obj / the GUI can grow the box as well (see Roo below).
The portal graph after the room records is decoded too: 2 endpoints per door plus u32
links of (roomIndex << 16) | portalIndex; the room tag's high byte is its door count.

CLI
    python collision.py info   <file.cvhm>
    python collision.py roo    <Global.ROO>       # rooms + decoded portals
    python collision.py export <file.cvhm|folder> <out_dir>
"""
import collections
import math
import os
import struct

MAGIC_V1 = 0xCCCC0104
MAGIC_V2 = 0xCCCC0204
SCALE = 4096.0
INF = 1 << 30
# The triangle attribute is a u16, but only two values occur in all 167 retail files:
# 0x0000 and 0x8000.  It is therefore a single boolean bit.  Its exact in-game meaning
# is still unknown (flagged tris are mostly vertical and have balanced winding, unlike
# the inward-facing majority of unflagged tris).  Never guess a label in UI/docs.
CVHM_FLAG_BIT15 = 0x8000


class CollisionError(Exception):
    pass


def name_hash(name):
    """Java-style string hash used in the file trailer."""
    h = 0
    for ch in name.encode('latin-1', 'replace'):
        h = (h * 31 + ch) & 0xFFFFFFFF
    return h


class Mesh:
    """One collision mesh. Vertices are raw fixed-point integers (value * 4096)."""

    def __init__(self, name, verts, tris, attrs=None):
        self.name = name
        self.verts = verts                      # [(x, y, z)] ints
        self.tris = tris                        # [(a, b, c)] ints
        self.attrs = list(attrs) if attrs else [0] * len(tris)

    def points(self):
        return [(x / SCALE, y / SCALE, z / SCALE) for x, y, z in self.verts]

    def bounds(self):
        """(min, max) as float triples, or None when empty."""
        if not self.verts:
            return None
        xs = [v[0] for v in self.verts]
        ys = [v[1] for v in self.verts]
        zs = [v[2] for v in self.verts]
        return ((min(xs) / SCALE, min(ys) / SCALE, min(zs) / SCALE),
                (max(xs) / SCALE, max(ys) / SCALE, max(zs) / SCALE))

    def triangles(self):
        """World-space float triangles (for previews)."""
        p = self.points()
        return [(p[a], p[b], p[c]) for a, b, c in self.tris]

    def stats(self):
        n = len(self.tris)
        return {'name': self.name, 'verts': len(self.verts), 'tris': n, 'bounds': self.bounds()}


class Collision:
    def __init__(self, version, meshes, path=None):
        self.version = version
        self.meshes = meshes
        self.path = path
        if version == 1 and len(meshes) != 1:
            raise CollisionError('a version 1 collision file must have exactly one mesh')

    def triangles(self):
        tris = []
        for m in self.meshes:
            tris.extend(m.triangles())
        return tris

    def bounds(self):
        bs = [m.bounds() for m in self.meshes if m.verts]
        if not bs:
            return None
        return (tuple(min(b[0][k] for b in bs) for k in range(3)),
                tuple(max(b[1][k] for b in bs) for k in range(3)))

    def names(self):
        return [m.name for m in self.meshes]

    def flag_counts(self):
        """Counter of triangle attribute values across every mesh."""
        counts = collections.Counter()
        for m in self.meshes:
            counts.update(m.attrs)
        return counts

    def flagged_triangles(self):
        """Readable summary of the per-triangle attribute bit (semantics unknown)."""
        counts = self.flag_counts()
        total = sum(counts.values())
        return ', '.join('%s: %d' % (
            '0x%04x' % flag if flag else '0', n) for flag, n in sorted(counts.items())) + (
            ' (bit 15 set: %d/%d)' % (counts.get(CVHM_FLAG_BIT15, 0), total) if total else '')

    def write(self, path=None):
        path = path or self.path
        if not path:
            raise CollisionError('no output path')
        data = build_bytes(self)
        with open(path, 'wb') as f:
            f.write(data)
        return len(data)


# --------------------------------------------------------------------- reading
def _read_mesh(data, o, n, t, b, name):
    if n == 0 and t == 0:
        return Mesh(name, [], []), o
    verts = [struct.unpack_from('<3i', data, o + i * 12) for i in range(n)]
    o += n * 12
    tris, attrs = [], []
    for i in range(t):
        a, bb, c, fl = struct.unpack_from('<4H', data, o + i * 8)
        if a >= n or bb >= n or c >= n:
            raise CollisionError('triangle %d has a vertex index outside the vertex list' % i)
        tris.append((a, bb, c))
        attrs.append(fl)
    o += t * 8
    o += b * 32
    return Mesh(name, verts, tris, attrs), o


def parse_bytes(data, path=''):
    if len(data) < 0x28:
        raise CollisionError('file is too small to be a collision file')
    magic = struct.unpack_from('<I', data, 0)[0]
    if magic == MAGIC_V1:
        n, t, b, _one = struct.unpack_from('<4H', data, 0x14)
        if b != max(0, t - 1):
            raise CollisionError('unexpected BVH node count (%d for %d triangles)' % (b, t))
        o = 0x28
        # the name comes last, so read the meshes first with a placeholder
        mesh, o = _read_mesh(data, o, n, t, b, '')
        try:
            _h, _zero = struct.unpack_from('<IH', data, o)
            raw = data[o + 6:].split(b'\x00', 1)[0]
            mesh.name = raw.decode('latin-1') or os.path.splitext(os.path.basename(path))[0]
        except Exception:
            mesh.name = os.path.splitext(os.path.basename(path))[0] or 'collision'
        return Collision(1, [mesh], path)
    if magic == MAGIC_V2:
        n1, t1, b1, _ = struct.unpack_from('<4H', data, 0x14)
        n2, t2, b2, _ = struct.unpack_from('<4H', data, 0x28)
        if b1 != max(0, t1 - 1) or b2 != max(0, t2 - 1):
            raise CollisionError('unexpected BVH node count')
        o = 0x3C
        m1, o = _read_mesh(data, o, n1, t1, b1, 'T_mesh')
        m2, o = _read_mesh(data, o, n2, t2, b2, 'H_mesh')
        try:
            h1, h2, zero, nlen = struct.unpack_from('<IIHH', data, o)
            rest = data[o + 12:].split(b'\x00')
            if rest and rest[0]:
                m1.name = rest[0].decode('latin-1')
            if len(rest) > 1 and rest[1]:
                m2.name = rest[1].decode('latin-1')
        except Exception:
            pass
        return Collision(2, [m1, m2], path)
    raise CollisionError('not a collision file (magic 0x%08X)' % magic)


def parse(path):
    with open(path, 'rb') as f:
        data = f.read()
    return parse_bytes(data, path)


# --------------------------------------------------------------------- BVH
def _build_bvh(verts, tris):
    """Return [(child0, child1, cx, cy, cz, hx, hy, hz)] with child0 at node 0."""
    if len(tris) < 2:
        raise CollisionError('a collision mesh needs at least 2 triangles (this one has %d)' % len(tris))
    cent = [((verts[a][0] + verts[b][0] + verts[c][0]) / 3.0,
             (verts[a][1] + verts[b][1] + verts[c][1]) / 3.0,
             (verts[a][2] + verts[b][2] + verts[c][2]) / 3.0) for a, b, c in tris]
    nodes = []

    def tri_box(i):
        mn = [INF, INF, INF]
        mx = [-INF, -INF, -INF]
        for vi in tris[i]:
            p = verts[vi]
            for k in range(3):
                if p[k] < mn[k]:
                    mn[k] = p[k]
                if p[k] > mx[k]:
                    mx[k] = p[k]
        return mn, mx

    def rec(idxs):
        if len(idxs) == 1:
            return (idxs[0] << 5) | 1, tri_box(idxs[0])
        node_index = len(nodes)
        nodes.append(None)
        mn = [INF, INF, INF]
        mx = [-INF, -INF, -INF]
        for i in idxs:
            b = tri_box(i)
            for k in range(3):
                mn[k] = min(mn[k], b[0][k])
                mx[k] = max(mx[k], b[1][k])
        ext = [mx[k] - mn[k] for k in range(3)]
        axis = ext.index(max(ext))
        idxs.sort(key=lambda i: cent[i][axis])
        mid = len(idxs) // 2
        c0, b0 = rec(idxs[:mid])
        c1, b1 = rec(idxs[mid:])
        # the box must contain both children, so take their union (not the raw triangle bounds)
        mn = [min(b0[0][k], b1[0][k]) for k in range(3)]
        mx = [max(b0[1][k], b1[1][k]) for k in range(3)]
        ct = [(mn[k] + mx[k]) // 2 for k in range(3)]
        hf = [max(mx[k] - ct[k], ct[k] - mn[k]) for k in range(3)]
        nodes[node_index] = (c0, c1, ct[0], ct[1], ct[2], hf[0], hf[1], hf[2])
        return node_index << 1, ((ct[0] - hf[0], ct[1] - hf[1], ct[2] - hf[2]),
                                 (ct[0] + hf[0], ct[1] + hf[1], ct[2] + hf[2]))

    rec(list(range(len(tris))))
    return nodes


def _mesh_bytes(mesh):
    n = len(mesh.verts)
    t = len(mesh.tris)
    if n > 0xFFFF:
        raise CollisionError('too many vertices (the format stores 16-bit indices)')
    if t > 0xFFFF:
        raise CollisionError('too many triangles (the format stores 16-bit indices)')
    out = bytearray()
    for x, y, z in mesh.verts:
        out += struct.pack('<3i', x, y, z)
    for (a, b, c), fl in zip(mesh.tris, mesh.attrs):
        out += struct.pack('<4H', a, b, c, fl)
    nodes = _build_bvh(mesh.verts, mesh.tris)
    for node in nodes:
        out += struct.pack('<IIiiiiii', *node)
    return bytes(out)


def build_bytes(coll):
    if coll.version not in (1, 2):
        raise CollisionError('unsupported collision version %r' % coll.version)
    if len(coll.meshes) != coll.version:
        raise CollisionError('version %d needs %d mesh(es), got %d'
                             % (coll.version, coll.version, len(coll.meshes)))
    out = bytearray()
    out += struct.pack('<I', MAGIC_V1 if coll.version == 1 else MAGIC_V2)
    out += b'\x00' * 16
    for m in coll.meshes:
        t = len(m.tris)
        if t < 2:
            raise CollisionError('%s: a collision mesh needs at least 2 triangles' % (m.name or 'mesh'))
        out += struct.pack('<4H', len(m.verts), t, t - 1, 1)
        out += b'\x00' * 12
    for m in coll.meshes:
        out += _mesh_bytes(m)
    if coll.version == 1:
        name = coll.meshes[0].name or 'COLLISION'
        out += struct.pack('<IH', name_hash(name), 0)
        out += name.encode('latin-1', 'replace') + b'\x00'
    else:
        names = [(m.name or ('T_MESH' if i == 0 else 'H_MESH')) for i, m in enumerate(coll.meshes)]
        out += struct.pack('<IIHH', name_hash(names[0]), name_hash(names[1]), 0, len(names[0]))
        out += names[0].encode('latin-1', 'replace') + b'\x00'
        out += names[1].encode('latin-1', 'replace') + b'\x00'
    return bytes(out)


# --------------------------------------------------------------------- OBJ
def export_obj(coll, path):
    """Write the collision mesh as an OBJ (one object per mesh, world coordinates)."""
    lines = ['# GoldenEye: Rogue Agent collision (%d mesh(es))' % len(coll.meshes),
             '# 1 OBJ unit = 1 collision unit = 1 render-model unit']
    off = 1
    for m in coll.meshes:
        lines.append('o %s' % (m.name or 'collision'))
        for x, y, z in m.verts:
            lines.append('v %.6f %.6f %.6f' % (x / SCALE, y / SCALE, z / SCALE))
        for a, b, c in m.tris:
            lines.append('f %d %d %d' % (off + a, off + b, off + c))
        off += len(m.verts)
    with open(path, 'w', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return {'meshes': len(coll.meshes), 'tris': sum(len(m.tris) for m in coll.meshes),
            'verts': sum(len(m.verts) for m in coll.meshes)}


class ObjData:
    def __init__(self):
        self.verts = []            # [(x, y, z)] floats
        self.groups = []           # [(name, [(a, b, c)])] vertex indices


def read_obj(path):
    ob = ObjData()
    cur = None
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line or line[0] == '#':
                continue
            parts = line.split()
            tag = parts[0]
            if tag == 'v':
                try:
                    ob.verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except (IndexError, ValueError):
                    raise CollisionError('bad vertex line: %s' % line[:80])
            elif tag in ('o', 'g'):
                name = line[2:].strip() or 'group%d' % len(ob.groups)
                if cur is None or cur[1]:
                    cur = [name, []]
                    ob.groups.append(cur)
                else:
                    cur[0] = name
            elif tag == 'f':
                ids = []
                for p in parts[1:]:
                    s = p.split('/')[0]
                    try:
                        i = int(s)
                    except ValueError:
                        raise CollisionError('bad face line: %s' % line[:80])
                    if i < 0:
                        i = len(ob.verts) + i
                    else:
                        i -= 1
                    if i < 0 or i >= len(ob.verts):
                        raise CollisionError('face refers to a missing vertex: %s' % line[:80])
                    ids.append(i)
                if len(ids) < 3:
                    continue
                if cur is None:
                    cur = ['geometry', []]
                    ob.groups.append(cur)
                for k in range(1, len(ids) - 1):          # triangulate a fan
                    cur[1].append((ids[0], ids[k], ids[k + 1]))
    ob.groups = [g for g in ob.groups if g[1]]
    if not ob.groups:
        raise CollisionError('the OBJ has no faces')
    if not ob.verts:
        raise CollisionError('the OBJ has no vertices')
    return ob


def _quantize(v):
    i = int(round(v * SCALE))
    if i < -0x7FFFFFFF or i > 0x7FFFFFFF:
        raise CollisionError('coordinate out of range (%g)' % v)
    return i


def _mesh_from_faces(name, all_verts, faces, attr_map=None, offset=(0.0, 0.0, 0.0)):
    """Weld by position, drop degenerate triangles, build a Mesh."""
    ox, oy, oz = offset
    remap = {}
    verts = []
    tris = []
    attrs = []
    for f in faces:
        ids = []
        for gi in f:
            x, y, z = all_verts[gi]
            key = (_quantize(x + ox), _quantize(y + oy), _quantize(z + oz))
            if key not in remap:
                remap[key] = len(verts)
                verts.append(key)
            ids.append(remap[key])
        a, b, c = ids
        if a == b or b == c or a == c:
            continue
        pa, pb, pc = verts[a], verts[b], verts[c]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        if (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) == (0, 0, 0):
            continue                                  # zero-area, useless for collision
        tris.append((a, b, c))
        attrs.append(0)
    mesh = Mesh(name, verts, tris, attrs)
    if attr_map:
        for i, (a, b, c) in enumerate(tris):
            mesh.attrs[i] = attr_map.get(_centroid_key(verts, (a, b, c)), 0)
    return mesh


def _centroid_key(verts, tri):
    a, b, c = verts[tri[0]], verts[tri[1]], verts[tri[2]]
    return (a[0] + b[0] + c[0], a[1] + b[1] + c[1], a[2] + b[2] + c[2])


def _attr_map(meshes, offset=(0.0, 0.0, 0.0)):
    """Triangle centroid -> flag, so unchanged geometry keeps its original surface flags."""
    amap = {}
    ox, oy, oz = (_quantize(-offset[0]), _quantize(-offset[1]), _quantize(-offset[2]))
    for m in meshes:
        for (a, b, c), fl in zip(m.tris, m.attrs):
            if not fl:
                continue
            va, vb, vc = m.verts[a], m.verts[b], m.verts[c]
            key = (va[0] + vb[0] + vc[0] + ox * 3,
                   va[1] + vb[1] + vc[1] + oy * 3,
                   va[2] + vb[2] + vc[2] + oz * 3)
            amap[key] = fl
    return amap


def _faces_for_meshes(ob, names):
    """Pick which OBJ objects go into which collision mesh."""
    groups = ob.groups
    if len(names) > 1:
        picked = []
        for nm in names:
            fs = []
            for gname, faces in groups:
                if gname.strip().lower() == nm.strip().lower():
                    fs.extend(faces)
            picked.append(fs)
        if all(picked):
            return picked
    allf = [f for _n, faces in groups for f in faces]
    return [list(allf) for _nm in names]


def generate_from_obj(obj_path, target_path=None, mode='auto', offset=(0.0, 0.0, 0.0), log=None):
    """Build a Collision from an OBJ.

    mode 'auto'  : move the OBJ so its bounding-box centre sits on the old collision's centre
    mode 'as-is' : use the OBJ coordinates unchanged (the OBJ is already in collision space)

    Returns (Collision, info dict).
    """
    log = log or (lambda *a: None)
    ob = read_obj(obj_path)
    old = None
    if target_path and os.path.exists(target_path):
        old = parse(target_path)
    version = old.version if old else 1
    names = old.names() if old else [
        os.path.splitext(os.path.basename(target_path or obj_path))[0].upper()]

    # bounding box of everything in the OBJ
    xs = [v[0] for v in ob.verts]
    ys = [v[1] for v in ob.verts]
    zs = [v[2] for v in ob.verts]
    nmin = (min(xs), min(ys), min(zs))
    nmax = (max(xs), max(ys), max(zs))
    ncenter = tuple((nmin[k] + nmax[k]) / 2 for k in range(3))

    move = (0.0, 0.0, 0.0)
    if mode == 'auto' and old is not None:
        ob_b = old.bounds()
        if ob_b:
            ocenter = tuple((ob_b[0][k] + ob_b[1][k]) / 2 for k in range(3))
            move = tuple(ocenter[k] - ncenter[k] for k in range(3))
            log('collision: moving the OBJ by (%.2f, %.2f, %.2f) to the old collision position'
                % move)
    move = tuple(move[k] + offset[k] for k in range(3))

    per_mesh = _faces_for_meshes(ob, names)
    attr_map = _attr_map(old.meshes, move) if old else None
    meshes = []
    for nm, faces in zip(names, per_mesh):
        if not faces:
            raise CollisionError('no geometry left for mesh %s' % nm)
        mesh = _mesh_from_faces(nm, ob.verts, faces, attr_map, move)
        if len(mesh.tris) < 2:
            raise CollisionError('%s needs at least 2 non-degenerate triangles (got %d)'
                                 % (nm, len(mesh.tris)))
        meshes.append(mesh)
    coll = Collision(version, meshes, target_path)
    info = {'mode': mode, 'move': move, 'obj_bounds': (nmin, nmax),
            'tris': sum(len(m.tris) for m in meshes),
            'verts': sum(len(m.verts) for m in meshes),
            'old_bounds': old.bounds() if old else None,
            'names': names, 'version': version}
    return coll, info


# ---------------------------------------------------------------- Global.ROO
#
# A level folder has a Global.ROO next to its .CVHM files.  It holds one axis-aligned
# box per room and the game only runs collision for the room the player is in, so a
# .CVHM that grows past its room box stops working outside the old extents.
#
# Layout (version 4):
#     0x00  u16 version (4), u16 roomCount, u16 count1, u16 count2
#     0x08  i32[6]  box of room 0                       (min xyz, max xyz, * 4096)
#     0x20  for room i: u32 tag = (flags << 16) | nameOffset   (name of room i)
#                       u32 unknown
#                       i32[6] box of room i+1            (the last box is unused)
#     then  portal data, then the NUL separated room names.
#
# The room name offsets point into the name table at the end of the file.  Boxes are
# 12.12 fixed point, exactly like the collision vertices.  One-room levels have no
# record at all: the name table starts right after room 0's box.

ROO_VERSION = 4
ROO_RECORD = 0x20
ROO_RECORD_SIZE = 0x20
# After the room records come two fully decoded tables (verified on all 18 levels):
#   countA portals: 2 x 12.12 xyz (the two doorway ends) + one u32 (always 0 in retail)
#   countB = 2 * countA u32 links: (roomIndex << 16) | portalIndex, two per portal
ROO_PORTAL_SIZE = 28
ROO_PORTAL_LINK_SHIFT = 16


def _roo_fixed(v):
    return int(math.floor(v * SCALE + 1e-4))


def _roo_fixed_hi(v):
    return int(math.ceil(v * SCALE - 1e-4))


def _roo_unfixed(v):
    return v / SCALE


def _roo_names(data, offsets):
    """Decode the packed room-name string table the record offsets point into."""
    n = len(data)
    runs = []
    i = ROO_RECORD
    while i < n:
        if 32 <= data[i] < 127:
            j = i
            while j < n and 32 <= data[j] < 127:
                j += 1
            if j - i >= 2 and (j >= n or data[j] == 0):
                runs.append((i, j))
            i = j
        else:
            i += 1
    starts = set(s for s, _e in runs)
    want = sorted(set(offsets))
    base = None
    for cand, _end in runs:
        if not all(cand + o in starts for o in want):
            continue
        packed = True
        for k in range(len(want) - 1):
            z = data.find(b'\x00', cand + want[k])
            if z < 0 or cand + want[k + 1] != z + 1:
                packed = False
                break
        if packed:
            if base is not None:
                return [''] * len(offsets)         # ambiguous: leave the names empty
            base = cand
    if base is None:
        return [''] * len(offsets)
    out = []
    for o in offsets:
        p = base + o
        z = data.find(b'\x00', p)
        if z < 0:
            z = n
        out.append(data[p:z].decode('latin-1') if 0 < z - p <= 40 else '')
    return out


class Roo:
    """The room table of a level's Global.ROO: one bounding box per room."""

    def __init__(self, data, path=''):
        if len(data) < ROO_RECORD + 8:
            raise CollisionError('file is too small to be a Global.ROO')
        ver, nr = struct.unpack_from('<2H', data, 0)
        if ver != ROO_VERSION:
            raise CollisionError('unsupported Global.ROO version %d' % ver)
        # room 0's name tag is at 0x20; the smallest file (one room) is 0x28 + the name
        if nr == 0 or 32 * nr + 8 > len(data):
            raise CollisionError('Global.ROO has a bad room count (%d)' % nr)
        self.path = path
        self.data = bytearray(data)
        self.version = ver
        self.room_count = nr
        self.counts = struct.unpack_from('<2H', data, 4)
        self.rooms = []
        tags = []
        for i in range(nr):
            rec = ROO_RECORD + ROO_RECORD_SIZE * i
            tag = struct.unpack_from('<I', data, rec)[0]
            tags.append(tag)
            off = 8 if i == 0 else ROO_RECORD + ROO_RECORD_SIZE * (i - 1) + 8
            raw = struct.unpack_from('<6i', data, off)
            self.rooms.append({'index': i, 'name': '', 'name_offset': tag & 0xFFFF,
                               'flags': tag >> 16, 'record_offset': rec,
                               'box_offset': off, 'box_i': raw,
                               'box': [_roo_unfixed(v) for v in raw]})
        for room, nm in zip(self.rooms, _roo_names(data, [t & 0xFFFF for t in tags])):
            room['name'] = nm
        self.portals = self._read_portals(data, nr, self.counts[0], self.counts[1])
        # The room tag's high byte is the number of portals touching that room:
        # tag = (portalCount << 24) | nameOffset, i.e. flags == portalCount << 8.
        # Verified on all 167 rooms of the 18 retail levels.
        for room in self.rooms:
            room['portal_count'] = room['flags'] >> 8
            room['portal_indices'] = [p['index'] for p in self.portals
                                      if room['index'] in p['rooms']]

    def _read_portals(self, data, nr, count_a, count_b):
        """Decode the door/portal graph stored after the room records.

        Returns a list of portals, each with the two 12.12 endpoints (raw and world
        space), the two room indices it connects, the reserved word and its offset.
        Corrupt/truncated tables are ignored rather than breaking box editing.
        """
        # room 0's box is in the header, so the records end at 0x08 + 0x20*N
        off = 0x08 + ROO_RECORD_SIZE * nr
        if count_a < 0 or count_b < 0 or off + count_a * ROO_PORTAL_SIZE + count_b * 4 > len(data):
            return []
        portals = []
        for g in range(count_a):
            pts = list(struct.unpack_from('<6i', data, off))
            reserved = struct.unpack_from('<I', data, off + 24)[0]
            portals.append({'index': g, 'record_offset': off,
                            'points': [tuple(pts[0:3]), tuple(pts[3:6])],
                            'world': [tuple(v / SCALE for v in pts[0:3]),
                                      tuple(v / SCALE for v in pts[3:6])],
                            'rooms': [], 'reserved': reserved})
            off += ROO_PORTAL_SIZE
        links = struct.unpack_from('<%dI' % count_b, data, off) if count_b else ()
        for link in links:
            portal = link & 0xFFFF
            room = link >> ROO_PORTAL_LINK_SHIFT
            if 0 <= portal < len(portals) and 0 <= room < nr:
                portals[portal]['rooms'].append(room)
        return portals

    def portal_descriptions(self):
        """One readable line per portal, for the info panel / tooling.

        The two endpoints sit on the shared face of the two rooms, so the link
        table is treated as an unordered connection rather than one room per end.
        """
        out = []
        for p in self.portals:
            ends = ['(%.2f, %.2f, %.2f)' % xyz for xyz in p['world']]
            rooms = []
            for room in p['rooms']:
                name = self.rooms[room]['name'] if 0 <= room < len(self.rooms) else ''
                rooms.append('%d%s' % (room, ' %s' % name if name else ''))
            out.append('portal %2d: %s  <->  %s   [%s]' %
                       (p['index'], ends[0], ends[1], ', '.join(rooms) or 'no link'))
        return out

    def to_bytes(self):
        return bytes(self.data)

    def write(self, path=None):
        path = path or self.path
        with open(path, 'wb') as f:
            f.write(self.data)
        return len(self.data)

    @staticmethod
    def violation(room, bmin, bmax):
        """How far the geometry sticks out of the room box (>0 = outside)."""
        b = room['box']
        return max(max(b[k] - bmin[k], bmax[k] - b[k + 3]) for k in range(3))

    @staticmethod
    def size_error(room, bmin, bmax):
        b = room['box']
        return max(abs((b[k + 3] - b[k]) - (bmax[k] - bmin[k])) for k in range(3))

    def find_room(self, bounds, hints=()):
        """Pick the room a collision file belongs to.

        bounds  : (min, max) of the collision geometry before the edit, or None
        hints   : possible room names (file name, mesh names, ...)

        Returns (room dict, reason string).  A matching name wins; otherwise the box
        that contains the old geometry with the closest size is used.
        """
        hset = set(h for h in (_clean_room_name(x) for x in hints) if h)
        named = [r for r in self.rooms if r['name'].upper() in hset]
        if len(named) == 1:
            return named[0], 'name'          # the file names match the room names 1:1
        if named:
            room = named[0]
            if bounds is None or self.violation(room, bounds[0], bounds[1]) <= 8.0:
                return room, 'name'
        if bounds is None:
            return (named[0], 'name') if named else (None, '')
        bmin, bmax = bounds
        ranked = sorted(
            (round(self.violation(r, bmin, bmax), 2),
             round(self.size_error(r, bmin, bmax), 2), r['index'])
            for r in self.rooms)
        viol, _size, idx = ranked[0]
        if viol > 8.0:
            return None, ''
        return self.rooms[idx], 'position'

    def set_box(self, index, bmin, bmax):
        """Set room `index`'s box exactly (min xyz, max xyz).  True when it changed."""
        room = self.rooms[index]
        raw = [_roo_fixed(bmin[0]), _roo_fixed(bmin[1]), _roo_fixed(bmin[2]),
               _roo_fixed_hi(bmax[0]), _roo_fixed_hi(bmax[1]), _roo_fixed_hi(bmax[2])]
        if list(room['box_i']) == raw:
            return False
        struct.pack_into('<6i', self.data, room['box_offset'], *raw)
        room['box_i'] = tuple(raw)
        room['box'] = [_roo_unfixed(v) for v in raw]
        return True

    def grow(self, index, bmin, bmax):
        """Grow room `index`'s box so it contains bmin..bmax.  True when it changed."""
        room = self.rooms[index]
        old = room['box_i']
        want = [_roo_fixed(bmin[0]), _roo_fixed(bmin[1]), _roo_fixed(bmin[2]),
                _roo_fixed_hi(bmax[0]), _roo_fixed_hi(bmax[1]), _roo_fixed_hi(bmax[2])]
        new = [min(old[k], want[k]) for k in range(3)] + \
              [max(old[k + 3], want[k + 3]) for k in range(3)]
        if list(old) == new:
            return False
        struct.pack_into('<6i', self.data, room['box_offset'], *new)
        room['box_i'] = tuple(new)
        room['box'] = [_roo_unfixed(v) for v in new]
        return True


def _clean_room_name(name):
    n = (name or '').strip().upper()
    for pre in ('H_', 'T_'):
        if n.startswith(pre) and len(n) > len(pre):
            n = n[len(pre):]
    return n


def _fmt_box(box):
    return '[%s]..[%s]' % (', '.join('%.2f' % v for v in box[:3]),
                           ', '.join('%.2f' % v for v in box[3:]))


def grow_level_box(cvhm_path, old_bounds, new_bounds, extra_names=(), log=None):
    """Grow the room box in the Global.ROO that sits next to a .CVHM.

    old_bounds / new_bounds : (min, max) float triples in level coordinates, or None
    extra_names            : more names the room could have (mesh names)

    Returns (roo_path, data, info) or None when there is no Global.ROO / no match.
    The caller is responsible for writing the data (so it can make a backup first).
    """
    log = log or (lambda *a: None)
    folder = os.path.dirname(os.path.abspath(cvhm_path))
    roo_path = os.path.join(folder, 'Global.ROO')
    if not os.path.exists(roo_path):
        log('level bounds: %s has no Global.ROO, so the room box cannot be grown.'
            % os.path.dirname(cvhm_path))
        return None
    try:
        roo = Roo(open(roo_path, 'rb').read(), roo_path)
    except (CollisionError, OSError) as e:
        log('level bounds: could not read %s (%s)' % (os.path.basename(roo_path), e))
        return None
    stem = os.path.splitext(os.path.basename(cvhm_path))[0]
    room, reason = roo.find_room(old_bounds, [stem] + list(extra_names))
    if room is None:
        log('level bounds: no room in %s matches %s; collision may stop at the old extents.'
            % (os.path.basename(roo_path), stem))
        return None
    old_box = list(room['box'])
    changed = False
    if new_bounds:
        changed = roo.grow(room['index'], new_bounds[0], new_bounds[1])
    info = {'room': room['name'] or ('room %d' % room['index']), 'index': room['index'],
            'old_box': old_box, 'new_box': list(room['box']), 'changed': changed,
            'reason': reason, 'rooms': roo.room_count}
    if reason == 'position':
        log('level bounds: no room in %s is named %s, using the nearest box (room %s)'
            % (os.path.basename(roo_path), stem, info['room']))
    if changed:
        log('level bounds: grew %s room %s  %s -> %s'
            % (os.path.basename(roo_path), info['room'], _fmt_box(old_box),
               _fmt_box(info['new_box'])))
    else:
        log('level bounds: %s room %s already covers the new collision (%s)'
            % (os.path.basename(roo_path), info['room'], _fmt_box(info['new_box'])))
    return roo_path, roo.to_bytes(), info


# --------------------------------------------------------------------- CLI
def _print_info(c):
    print('%s  v%d, %d mesh(es)' % (c.path or '(memory)', c.version, len(c.meshes)))
    for m in c.meshes:
        b = m.bounds()
        print('  %-24s %5d verts %5d tris  box X %.2f..%.2f  Y %.2f..%.2f  Z %.2f..%.2f'
              % (m.name, len(m.verts), len(m.tris),
                 b[0][0], b[1][0], b[0][1], b[1][1], b[0][2], b[1][2]))


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, src = sys.argv[1], sys.argv[2]
    if cmd == 'info':
        _print_info(parse(src))
    elif cmd == 'roo':
        r = Roo(open(src, 'rb').read(), src)
        print('%s  v%d, %d rooms, %d portals' %
              (src, r.version, r.room_count, len(r.portals)))
        for room in r.rooms:
            print('  room %2d %-16s doors=%d box=%s' %
                  (room['index'], room['name'] or '(unnamed)', room['portal_count'],
                   _fmt_box(room['box'])))
        for line in r.portal_descriptions():
            print('  ' + line)
    elif cmd == 'export':
        out = sys.argv[3] if len(sys.argv) > 3 else 'collision_obj'
        files = [src] if os.path.isfile(src) else []
        if not files:
            for r, _ds, fs in os.walk(src):
                files += [os.path.join(r, f) for f in fs if f.lower().endswith('.cvhm')]
        base = os.path.dirname(os.path.abspath(src)) if os.path.isfile(src) else src
        bad = 0
        for p in sorted(files):
            rel = os.path.dirname(os.path.relpath(p, base))
            d = os.path.join(out, rel)
            os.makedirs(d, exist_ok=True)
            try:
                c = parse(p)
                name = os.path.splitext(os.path.basename(p))[0] + '_collision.obj'
                s = export_obj(c, os.path.join(d, name))
                print('exported', p, s['tris'], 'triangles')
            except Exception as e:
                bad += 1
                print('FAIL', p, e)
        print('done, %d failed' % bad)
    else:
        print(__doc__)
