#!/usr/bin/env python3
"""
nitro.py - parser for NITRO System 3D resources (NSBMD / NSBTX) as used in
GoldenEye: Rogue Agent (DS). Written to be tolerant: anything that doesn't
match the standard layout raises NitroError with an explanation, so odd files
can be found and studied instead of silently producing garbage.
"""
import struct, math

class NitroError(Exception):
    pass

U8 = lambda d, o: d[o]
U16 = lambda d, o: struct.unpack_from('<H', d, o)[0]
S16 = lambda d, o: struct.unpack_from('<h', d, o)[0]
U32 = lambda d, o: struct.unpack_from('<I', d, o)[0]
S32 = lambda d, o: struct.unpack_from('<i', d, o)[0]


# ----------------------------------------------------------------- dictionary
def parse_dict(d, off):
    """Nitro 'dictionary': returns [(name, entry_bytes), ...]"""
    if off + 8 > len(d):
        raise NitroError('dict out of range')
    cnt = d[off + 1]
    tree_size = U16(d, off + 6)
    p = off + tree_size
    esz = U16(d, p)
    p += 4
    names_at = p + cnt * esz
    out = []
    for i in range(cnt):
        name = d[names_at + i * 16: names_at + i * 16 + 16].split(b'\0')[0].decode('latin1')
        out.append((name, d[p + i * esz: p + (i + 1) * esz]))
    return out


# ------------------------------------------------------------------ textures
def rgb555(c):
    return ((c & 31) * 255 // 31, ((c >> 5) & 31) * 255 // 31, ((c >> 10) & 31) * 255 // 31)


class Texture:
    def __init__(self, name, fmt, w, h, color0_transp, data, idxdata, offset):
        self.name, self.fmt, self.w, self.h = name, fmt, w, h
        self.color0_transp = color0_transp
        self.data, self.idxdata, self.offset = data, idxdata, offset

    def decode(self, pal):
        """Return list of RGBA tuples (row-major), w*h. pal = list of RGB555 ints."""
        w, h, fmt, d = self.w, self.h, self.fmt, self.data
        px = [(0, 0, 0, 255)] * (w * h)

        def P(i):
            if i < len(pal):
                r, g, b = rgb555(pal[i])
                return (r, g, b, 255)
            return (255, 0, 255, 255)

        if fmt == 1:    # A3I5
            for i in range(w * h):
                v = d[i] if i < len(d) else 0
                a = ((v >> 5) * 255) // 7
                c = P(v & 31)
                px[i] = (c[0], c[1], c[2], a)
        elif fmt == 6:  # A5I3
            for i in range(w * h):
                v = d[i] if i < len(d) else 0
                a = ((v >> 3) * 255) // 31
                c = P(v & 7)
                px[i] = (c[0], c[1], c[2], a)
        elif fmt == 2:  # 4 colour
            for i in range(w * h):
                v = (d[i >> 2] >> ((i & 3) * 2)) & 3 if (i >> 2) < len(d) else 0
                c = P(v)
                px[i] = (c[0], c[1], c[2], 0 if (v == 0 and self.color0_transp) else 255)
        elif fmt == 3:  # 16 colour
            for i in range(w * h):
                v = (d[i >> 1] >> ((i & 1) * 4)) & 15 if (i >> 1) < len(d) else 0
                c = P(v)
                px[i] = (c[0], c[1], c[2], 0 if (v == 0 and self.color0_transp) else 255)
        elif fmt == 4:  # 256 colour
            for i in range(w * h):
                v = d[i] if i < len(d) else 0
                c = P(v)
                px[i] = (c[0], c[1], c[2], 0 if (v == 0 and self.color0_transp) else 255)
        elif fmt == 7:  # direct colour
            for i in range(w * h):
                if i * 2 + 1 < len(d):
                    v = U16(d, i * 2)
                    r, g, b = rgb555(v)
                    px[i] = (r, g, b, 255 if v & 0x8000 else 0)
        elif fmt == 5:  # 4x4 compressed
            bw = w // 4
            for by in range(h // 4):
                for bx in range(bw):
                    bi = by * bw + bx
                    if bi * 4 + 3 >= len(d) or bi * 2 + 1 >= len(self.idxdata):
                        continue
                    pi = U16(self.idxdata, bi * 2)
                    mode, poff = pi >> 14, (pi & 0x3FFF) * 2
                    def pc(k):
                        j = poff + k
                        return rgb555(pal[j]) if j < len(pal) else (255, 0, 255)
                    c0, c1 = pc(0), pc(1)
                    if mode == 0:
                        cols = [c0 + (255,), c1 + (255,), pc(2) + (255,), (0, 0, 0, 0)]
                    elif mode == 1:
                        mid = tuple((a + b) // 2 for a, b in zip(c0, c1))
                        cols = [c0 + (255,), c1 + (255,), mid + (255,), (0, 0, 0, 0)]
                    elif mode == 2:
                        cols = [c0 + (255,), c1 + (255,), pc(2) + (255,), pc(3) + (255,)]
                    else:
                        a = tuple((5 * x + 3 * y) // 8 for x, y in zip(c0, c1))
                        b = tuple((3 * x + 5 * y) // 8 for x, y in zip(c0, c1))
                        cols = [c0 + (255,), c1 + (255,), a + (255,), b + (255,)]
                    for ry in range(4):
                        row = d[bi * 4 + ry]
                        for rx in range(4):
                            px[(by * 4 + ry) * w + bx * 4 + rx] = cols[(row >> (rx * 2)) & 3]
        else:
            raise NitroError('unknown texture format %d' % fmt)
        return px


class TexBlock:
    """A parsed TEX0 block (embedded in NSBMD or stored in NSBTX)."""
    def __init__(self, d, base):
        if d[base:base + 4] != b'TEX0':
            raise NitroError('no TEX0 at %x' % base)
        self.d, self.base = d, base
        tex_info_ofs = U16(d, base + 0x0E)
        tex_data_ofs = U32(d, base + 0x14)
        comp_data_ofs = U32(d, base + 0x24)
        comp_idx_ofs = U32(d, base + 0x28)
        pal_info_ofs = U32(d, base + 0x34)
        pal_data_ofs = U32(d, base + 0x38)
        total = U32(d, base + 4)
        self.textures = []
        for name, e in parse_dict(d, base + tex_info_ofs):
            off = U16(e, 0) * 8
            param = U16(e, 2)
            fmt = (param >> 10) & 7
            w = 8 << ((param >> 4) & 7)
            h = 8 << ((param >> 7) & 7)
            c0t = bool((param >> 13) & 1)
            if fmt == 5:
                nbytes = w * h // 4
                data = d[base + comp_data_ofs + off: base + comp_data_ofs + off + nbytes]
                idx = d[base + comp_idx_ofs + off // 2: base + comp_idx_ofs + off // 2 + nbytes // 2]
            else:
                bpp = {1: 8, 2: 2, 3: 4, 4: 8, 6: 8, 7: 16}.get(fmt)
                if bpp is None:
                    raise NitroError('texture %s: bad format %d' % (name, fmt))
                nbytes = w * h * bpp // 8
                data = d[base + tex_data_ofs + off: base + tex_data_ofs + off + nbytes]
                idx = b''
            tx = Texture(name, fmt, w, h, c0t, data, idx, off)
            if fmt == 5:
                tx.file_off = base + comp_data_ofs + off
                tx.idx_off = base + comp_idx_ofs + off // 2
            else:
                tx.file_off = base + tex_data_ofs + off
                tx.idx_off = None
            tx.nbytes = nbytes
            self.textures.append(tx)
        self.palettes = {}
        self.pal_slots = {}          # name -> (absolute file offset, size in bytes)
        pal_ents = parse_dict(d, base + pal_info_ofs)
        # palette sizes aren't stored per entry; derive from the next offset
        offs = sorted(set(U16(e, 0) * 8 for _, e in pal_ents))
        end = total - pal_data_ofs
        for name, e in pal_ents:
            o = U16(e, 0) * 8
            nxt = [x for x in offs if x > o]
            e_ = nxt[0] if nxt else end
            raw = d[base + pal_data_ofs + o: base + pal_data_ofs + e_]
            self.palettes[name] = [U16(raw, i) for i in range(0, len(raw) - 1, 2)]
            self.pal_slots[name] = (base + pal_data_ofs + o, len(raw))
        self.tex_by_name = {t.name: t for t in self.textures}


# --------------------------------------------------------------- matrix utils
def mat_ident():
    return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]

def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]

def mat_apply(m, v):
    x, y, z = v
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
            m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
            m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3])

def mat_apply_dir(m, v):
    x, y, z = v
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z,
            m[1][0] * x + m[1][1] * y + m[1][2] * z,
            m[2][0] * x + m[2][1] * y + m[2][2] * z)


# ------------------------------------------------------------------ geometry
class Model:
    pass

def _find_node_dict(d, m, num_node, lo=0x20, hi=0x70):
    for p in range(m + lo, m + hi, 4):
        if d[p] == 0 and d[p + 1] == num_node and U16(d, p + 4) == 8:
            return p
    raise NitroError('node dict not found')


def parse_model(d, mpos, mdl_base):
    m = Model()
    m.size, sbc_o, mat_o, shp_o = struct.unpack_from('<IIII', d, mpos)
    m.pos, m.sbc_o, m.mat_o, m.shp_o, m.mdl_base = mpos, sbc_o, mat_o, shp_o, mdl_base
    # Find the info block. Variant A: info at +0x10 (no EVP offset field).
    # Variant B: EVP offset at +0x10, info at +0x14.
    m.variant = None
    for var, ioff in (('A', 0x10), ('B', 0x14)):
        num_node, num_mat, num_shp = d[mpos + ioff + 3], d[mpos + ioff + 4], d[mpos + ioff + 5]
        try:
            nd = _find_node_dict(d, mpos, num_node)
        except NitroError:
            continue
        m.variant, m.info_off, m.nd = var, ioff, nd
        break
    if m.variant is None:
        raise NitroError('cannot locate model info/node dict')
    i = mpos + m.info_off
    m.sbc_type, m.scaling_rule, m.texmtx_mode = d[i], d[i + 1], d[i + 2]
    m.num_node, m.num_mat, m.num_shp = d[i + 3], d[i + 4], d[i + 5]
    m.pos_scale = S32(d, i + 8) / 4096.0
    m.num_vtx, m.num_poly, m.num_tri, m.num_quad = struct.unpack_from('<HHHH', d, i + 0x10)

    # nodes
    m.nodes = []
    for name, e in parse_dict(d, m.nd):
        o = m.nd + U32(e, 0)
        m.nodes.append((name, parse_node(d, o)))

    # materials
    ms = mpos + mat_o
    tex_list_o, pal_list_o = U16(d, ms), U16(d, ms + 2)
    m.materials = []
    for name, e in parse_dict(d, ms + 4):
        o = ms + U32(e, 0)
        mat = dict(name=name, off=o, tex=None, pal=None,
                   param=U32(d, o + 0x14), plt_base=U16(d, o + 0x1C),
                   flag=U16(d, o + 0x1E),
                   polyattr=U32(d, o + 8), diffamb=U32(d, o + 4))
        mat['wrap'] = (mat['param'] >> 16) & 0xF      # bit0 repeatS, 1 repeatT, 2 flipS, 3 flipT
        fl, q = mat['flag'], o + 0x2C
        mat['tm_scale'], mat['tm_trans'] = (1.0, 1.0), (0.0, 0.0)
        if fl & 1:                                    # texture matrix in use
            if not fl & 2:
                mat['tm_scale'] = (S32(d, q) / 4096.0, S32(d, q + 4) / 4096.0); q += 8
            if not fl & 4:
                q += 4                                # rotation (sin, cos) - not applied yet
            if not fl & 8:
                mat['tm_trans'] = (S32(d, q) / 4096.0, S32(d, q + 4) / 4096.0); q += 8
        m.materials.append(mat)
    for name, e in parse_dict(d, ms + tex_list_o):
        lo, cnt = U16(e, 0), e[2]
        for k in range(cnt):
            mi = d[ms + lo + k]
            if mi < len(m.materials):
                m.materials[mi]['tex'] = name
    for name, e in parse_dict(d, ms + pal_list_o):
        lo, cnt = U16(e, 0), e[2]
        for k in range(cnt):
            mi = d[ms + lo + k]
            if mi < len(m.materials):
                m.materials[mi]['pal'] = name

    # shapes
    ss = mpos + shp_o
    m.shapes = []
    for name, e in parse_dict(d, ss + 0):
        o = ss + U32(e, 0)
        flag, dl_o, dl_sz = struct.unpack_from('<III', d, o + 4)
        m.shapes.append(dict(name=name, flag=flag, dl=d[o + dl_o: o + dl_o + dl_sz]))

    # render commands
    m.sbc = parse_sbc(d, mpos + sbc_o)
    return m


def parse_node(d, o):
    flag = U16(d, o)
    p = o + 4
    T = (0.0, 0.0, 0.0)
    R = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    S = (1.0, 1.0, 1.0)
    IS = (1.0, 1.0, 1.0)
    if not flag & 1:
        T = tuple(S32(d, p + 4 * k) / 4096.0 for k in range(3)); p += 12
    if not flag & 2:
        _00 = S16(d, o + 2) / 4096.0
        if flag & 8:
            A, B = S16(d, p) / 4096.0, S16(d, p + 2) / 4096.0; p += 4
            piv = (flag >> 4) & 0xF
            neg = bool(flag & 0x100)
            revc = bool(flag & 0x200)
            revd = bool(flag & 0x400)
            R = pivot_matrix(piv, neg, revc, revd, A, B)
        else:
            v = [S16(d, p + 2 * k) / 4096.0 for k in range(8)]; p += 16
            # file order: _00 (hdr), _01 _02 _10 _11 _12 _20 _21 _22
            _01, _02, _10, _11, _12, _20, _21, _22 = v
            R = [[_00, _01, _02], [_10, _11, _12], [_20, _21, _22]]
    if not flag & 4:
        S = tuple(S32(d, p + 4 * k) / 4096.0 for k in range(3))
        IS = tuple(S32(d, p + 12 + 4 * k) / 4096.0 for k in range(3)); p += 24
    return dict(flag=flag, T=T, R=R, S=S, IS=IS)


def pivot_matrix(piv, neg, revc, revd, A, B):
    """Rotation matrix with a single +/-1 'pivot' element at row piv//3, col piv%3."""
    sign = -1.0 if neg else 1.0
    r, c = piv // 3, piv % 3
    M = [[0.0] * 3 for _ in range(3)]
    M[r][c] = sign
    rows = [i for i in range(3) if i != r]
    cols = [j for j in range(3) if j != c]
    M[rows[0]][cols[0]] = A
    M[rows[0]][cols[1]] = B
    M[rows[1]][cols[0]] = -B if revc else B
    M[rows[1]][cols[1]] = -A if revd else A
    return M


def parse_sbc(d, p):
    cmds = []
    while True:
        c = d[p]
        b = c & 0x1F
        if b == 0x00:
            n = 1
        elif b == 0x01:
            cmds.append(('RET',)); break
        elif b == 0x02:
            cmds.append(('NODE', d[p + 1], d[p + 2])); n = 3
        elif b == 0x03:
            cmds.append(('MTX', d[p + 1])); n = 2
        elif b == 0x04:
            cmds.append(('MAT', d[p + 1], p + 1)); n = 2
        elif b == 0x05:
            cmds.append(('SHP', d[p + 1])); n = 2
        elif b == 0x06:
            n = 4
            node, parent = d[p + 1], d[p + 2]
            store = restore = None
            if c & 0x20:
                store = d[p + n]; n += 1
            if c & 0x40:
                restore = d[p + n]; n += 1
            cmds.append(('NODEDESC', node, parent, store, restore))
        elif b == 0x07 or b == 0x08:
            cmds.append(('BB', d[p + 1], p)); n = 2
        elif b == 0x09:
            cnt = d[p + 2]
            items = [(d[p + 3 + 3 * k], d[p + 4 + 3 * k], d[p + 5 + 3 * k]) for k in range(cnt)]
            cmds.append(('NODEMIX', d[p + 1], items)); n = 3 + 3 * cnt
        elif b == 0x0B:
            cmds.append(('POSSCALE', c)); n = 1
        else:
            raise NitroError('unknown SBC command 0x%02x at %x' % (c, p))
        p += n
        if len(cmds) > 100000:
            raise NitroError('SBC runaway')
    return cmds


# ------------------------------------------------- display list -> triangles
def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v & (1 << (bits - 1)) else v

CMD_PARAMS = {0x00: 0, 0x10: 1, 0x11: 0, 0x12: 1, 0x13: 1, 0x14: 1, 0x15: 0, 0x16: 16, 0x17: 12,
              0x18: 16, 0x19: 12, 0x1A: 9, 0x1B: 3, 0x1C: 3, 0x20: 1, 0x21: 1, 0x22: 1, 0x23: 2,
              0x24: 1, 0x25: 1, 0x26: 1, 0x27: 1, 0x28: 1, 0x29: 1, 0x2A: 1, 0x2B: 1, 0x30: 1,
              0x31: 1, 0x32: 1, 0x33: 1, 0x34: 32, 0x40: 1, 0x41: 0, 0x50: 1, 0x60: 1, 0x70: 3,
              0x71: 2, 0x72: 1}


def run_display_list(dl, pos_scale, mtx_for_restore=None):
    """
    Interpret a GX display list. Returns list of triangles; each triangle is
    3 vertices (x, y, z, s, t, nx, ny, nz, restore_idx) with raw (unscaled by
    node matrix) positions already multiplied by pos_scale.
    restore_idx is the last MTX_RESTORE index seen (for skinned meshes) or None.
    """
    tris = []
    n = len(dl) // 4
    words = struct.unpack_from('<%dI' % n, dl)
    i = 0
    cur = [0.0, 0.0, 0.0]
    st = (0.0, 0.0)
    nrm = (0.0, 0.0, 1.0)
    restore = None
    col = (1.0, 1.0, 1.0)
    prim = None
    verts = []
    flip = 0

    def emit_vertex():
        nonlocal verts, flip
        v = (cur[0] * pos_scale, cur[1] * pos_scale, cur[2] * pos_scale, st[0], st[1],
             nrm[0], nrm[1], nrm[2], restore, col)
        verts.append(v)
        k = len(verts)
        if prim == 0:
            if k == 3:
                tris.append(tuple(verts)); verts = []
        elif prim == 1:
            if k == 4:
                a, b, c, d_ = verts
                tris.append((a, b, c)); tris.append((a, c, d_)); verts = []
        elif prim == 2:
            if k >= 3:
                a, b, c = verts[-3:]
                tris.append((a, b, c) if flip == 0 else (b, a, c))
                flip ^= 1
        elif prim == 3:
            if k >= 4 and (k - 4) % 2 == 0:
                a, b, c, d_ = verts[-4:]
                tris.append((a, b, d_)); tris.append((a, d_, c))

    while i < n:
        w = words[i]; i += 1
        for sh in (0, 8, 16, 24):
            c = (w >> sh) & 0xFF
            if c == 0 and False:
                continue
            if c not in CMD_PARAMS:
                if c == 0:
                    continue
                raise NitroError('unknown GX command 0x%02x' % c)
            cnt = CMD_PARAMS[c]
            if i + cnt > n:
                if c == 0:
                    continue
                raise NitroError('display list truncated')
            par = words[i:i + cnt]; i += cnt
            if c == 0x14:
                restore = par[0] & 0x1F
            elif c == 0x20:
                col = ((par[0] & 31) / 31.0, ((par[0] >> 5) & 31) / 31.0, ((par[0] >> 10) & 31) / 31.0)
            elif c == 0x22:
                st = (sext(par[0], 16) / 16.0, sext(par[0] >> 16, 16) / 16.0)
            elif c == 0x21:
                nrm = (sext(par[0], 10) / 512.0, sext(par[0] >> 10, 10) / 512.0, sext(par[0] >> 20, 10) / 512.0)
            elif c == 0x23:
                cur = [sext(par[0], 16) / 4096.0, sext(par[0] >> 16, 16) / 4096.0, sext(par[1], 16) / 4096.0]
                emit_vertex()
            elif c == 0x24:
                cur = [sext(par[0], 10) / 64.0, sext(par[0] >> 10, 10) / 64.0, sext(par[0] >> 20, 10) / 64.0]
                emit_vertex()
            elif c == 0x25:
                cur[0] = sext(par[0], 16) / 4096.0; cur[1] = sext(par[0] >> 16, 16) / 4096.0
                emit_vertex()
            elif c == 0x26:
                cur[0] = sext(par[0], 16) / 4096.0; cur[2] = sext(par[0] >> 16, 16) / 4096.0
                emit_vertex()
            elif c == 0x27:
                cur[1] = sext(par[0], 16) / 4096.0; cur[2] = sext(par[0] >> 16, 16) / 4096.0
                emit_vertex()
            elif c == 0x28:
                cur[0] += sext(par[0], 10) / 4096.0
                cur[1] += sext(par[0] >> 10, 10) / 4096.0
                cur[2] += sext(par[0] >> 20, 10) / 4096.0
                emit_vertex()
            elif c == 0x40:
                prim = par[0] & 3; verts = []; flip = 0
            elif c == 0x41:
                prim = None; verts = []
    return tris


# --------------------------------------------------------------- file level
class NSBMD:
    def __init__(self, data):
        d = self.d = data
        if d[:4] != b'BMD0':
            raise NitroError('not BMD0')
        n = U16(d, 0x0E)
        offs = struct.unpack_from('<%dI' % n, d, 0x10)
        self.models, self.tex = [], None
        for o in offs:
            tag = d[o:o + 4]
            if tag == b'MDL0':
                for name, e in parse_dict(d, o + 8):
                    mo = o + U32(e, 0)
                    mdl = parse_model(d, mo, o)
                    mdl.name = name
                    self.models.append(mdl)
            elif tag == b'TEX0':
                self.tex = TexBlock(d, o)
            else:
                raise NitroError('unknown block %r' % tag)


class NSBTX:
    def __init__(self, data):
        d = self.d = data
        if d[:4] != b'BTX0':
            raise NitroError('not BTX0')
        n = U16(d, 0x0E)
        offs = struct.unpack_from('<%dI' % n, d, 0x10)
        self.tex = None
        for o in offs:
            if d[o:o + 4] == b'TEX0':
                self.tex = TexBlock(d, o)


def build_world_matrices(model):
    return [(x['si'], x['mi'], x['W']) for x in shape_draws(model)]


def shape_draws(model):
    """Evaluate SBC for static models. Returns list of (shape_idx, mat_idx, world_matrix)."""
    local = []
    for name, nd in model.nodes:
        T, R, S = nd['T'], nd['R'], nd['S']
        # the file stores the 3x3 in DS (column-major) order: M[r][c] = R[c][r]
        M = [[R[c][r] * S[c] for c in range(3)] + [T[r]] for r in range(3)] + [[0, 0, 0, 1]]
        local.append(M)
    world = [mat_ident() for _ in model.nodes]
    cur_node = 0
    cur_mat = 0
    cur_mat_pos = None
    out = []
    for c in model.sbc:
        k = c[0]
        if k == 'NODEDESC':
            _, node, parent, store, restore = c
            if parent == node:
                world[node] = local[node]
            else:
                P = world[parent]
                if model.scaling_rule == 1:            # Maya: cancel the parent's scale
                    IS = model.nodes[parent][1]['IS']
                    P = mat_mul(P, [[IS[0], 0, 0, 0], [0, IS[1], 0, 0], [0, 0, IS[2], 0], [0, 0, 0, 1]])
                world[node] = mat_mul(P, local[node])
            cur_node = node
        elif k == 'NODE':
            cur_node = c[1]
        elif k == 'MAT':
            cur_mat = c[1]
            cur_mat_pos = c[2]
        elif k == 'SHP':
            out.append(dict(si=c[1], mi=cur_mat, W=world[cur_node], node=cur_node,
                            mat_pos=cur_mat_pos, node_name=model.nodes[cur_node][0]))
    return out
