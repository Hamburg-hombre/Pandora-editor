#!/usr/bin/env python3
"""
nitro_write.py - write edits back into Rogue Agent files.
  import_png_texture : PNG -> existing texture slot of an .nsbmd/.nsbtx (any DS texture format)
  import_obj         : OBJ -> replaces the geometry of an .nsbmd (shapes named shpNNN_*)
All functions return new file bytes; the caller decides where to write them.
"""
import os, re, struct, math
from collections import OrderedDict, defaultdict
import numpy as np
from PIL import Image
from nitro import *
from scene import MatInfo

class ImportError_(Exception):
    pass


# ============================================================ colour helpers
def q555(r, g, b):
    return ((int(r) * 31 + 127) // 255) | (((int(g) * 31 + 127) // 255) << 5) | (((int(b) * 31 + 127) // 255) << 10)


def _quantize(rgb_img, n):
    """Median-cut quantise to <= n colours. Returns (index array HxW, list of (r,g,b))."""
    n = max(1, min(256, n))
    arr = np.asarray(rgb_img.convert('RGB'))
    if n == 1:
        return np.zeros(arr.shape[:2], int), [tuple(int(x) for x in arr.reshape(-1, 3).mean(0))]
    q = rgb_img.convert('RGB').quantize(colors=n, method=0, dither=0)      # 0 = median cut
    pal = q.getpalette() or []
    idx = np.asarray(q).astype(int)
    used = int(idx.max()) + 1
    return idx, [tuple(pal[3 * i:3 * i + 3]) for i in range(used)]


# ============================================================ texture encoding
def _prep_indexed(im, ncols, reserve_transparent):
    a = np.asarray(im)
    opaque = a[..., 3] >= 128
    if reserve_transparent and opaque.any():
        mean = a[..., :3][opaque].mean(0).astype(np.uint8)
        rgb = a[..., :3].copy()
        rgb[~opaque] = mean
        idx, cols = _quantize(Image.fromarray(rgb), ncols - 1)
        idx = np.where(opaque, idx + 1, 0)
        cols = [(0, 0, 0)] + cols
    else:
        idx, cols = _quantize(Image.fromarray(a[..., :3]), ncols)
    return idx, [q555(*c) for c in cols], a[..., 3]


def encode_texture(tex, im, slot_colors):
    """Encode RGBA PIL image (already tex.w x tex.h) in tex's format. Returns (data, idxdata, palette)."""
    im = im.convert('RGBA')
    fmt, w, h = tex.fmt, tex.w, tex.h
    a = np.asarray(im)
    if fmt == 7:
        r, g, b, al = (a[..., k].astype(int) for k in range(4))
        v = ((r * 31 + 127) // 255) | (((g * 31 + 127) // 255) << 5) | (((b * 31 + 127) // 255) << 10)
        v |= np.where(al >= 128, 0x8000, 0)
        return v.astype('<u2').tobytes(), b'', None
    if fmt in (1, 6):
        nmax, abits = (32, 3) if fmt == 1 else (8, 5)
        idx, pal, al = _prep_indexed(im, min(nmax, slot_colors), False)
        amax = (1 << abits) - 1
        a_q = (al.astype(int) * amax + 127) // 255
        shift = 5 if fmt == 1 else 3
        return ((a_q << shift) | idx).astype(np.uint8).tobytes(), b'', pal
    if fmt in (2, 3, 4):
        nmax = {2: 4, 3: 16, 4: 256}[fmt]
        idx, pal, _ = _prep_indexed(im, min(nmax, slot_colors), tex.color0_transp)
        f = idx.reshape(-1)
        if fmt == 4:
            data = f.astype(np.uint8).tobytes()
        elif fmt == 3:
            data = (f[0::2] | (f[1::2] << 4)).astype(np.uint8).tobytes()
        else:
            data = (f[0::4] | (f[1::4] << 2) | (f[2::4] << 4) | (f[3::4] << 6)).astype(np.uint8).tobytes()
        return data, b'', pal
    if fmt == 5:
        return encode_compressed(im, slot_colors)
    raise ImportError_('unsupported texture format %d' % fmt)


def _c8(c555):
    return rgb555(c555)


def encode_compressed(im, slot_colors):
    w, h = im.size
    bw, bh = w // 4, h // 4
    a = np.asarray(im.convert('RGBA')).astype(np.int32)
    K = max(1, slot_colors // 2)
    ends, trans = [], []
    for by in range(bh):
        for bx in range(bw):
            px = a[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4].reshape(16, 4)
            op = px[:, 3] >= 128
            trans.append(not op.all())
            if not op.any():
                ends.append((0, 0, 0, 0, 0, 0)); continue
            pts = px[op][:, :3].astype(np.float64)
            mean = pts.mean(0)
            if len(pts) > 1 and np.ptp(pts, axis=0).max() > 0:
                cov = np.cov((pts - mean).T)
                vals, vecs = np.linalg.eigh(np.atleast_2d(cov))
                axis = vecs[:, -1]
                proj = (pts - mean) @ axis
                lo, hi = pts[proj.argmin()], pts[proj.argmax()]
            else:
                lo = hi = mean
            c0 = [min(31, max(0, int(round(x * 31 / 255)))) for x in lo]
            c1 = [min(31, max(0, int(round(x * 31 / 255)))) for x in hi]
            if sum(c0) > sum(c1):
                c0, c1 = c1, c0
            ends.append(tuple(c0 + c1))
    E = np.array(ends, dtype=np.float64)
    uniq = OrderedDict()
    for e in ends:
        uniq.setdefault(e, len(uniq))
    if len(uniq) <= K:
        centers = [tuple(int(x) for x in e) for e in uniq]
        assign = [uniq[e] for e in ends]
    else:
        U = np.array(list(uniq.keys()), dtype=np.float64)
        counts = np.bincount([uniq[e] for e in ends], minlength=len(U)).astype(np.float64)
        rng = np.random.RandomState(1234)
        C = U[rng.choice(len(U), K, replace=False, p=counts / counts.sum())].copy()
        for _ in range(12):
            d2 = ((U[:, None, :] - C[None, :, :]) ** 2).sum(2)
            lab = d2.argmin(1)
            for k in range(K):
                mk = lab == k
                if mk.any():
                    C[k] = (U[mk] * counts[mk, None]).sum(0) / counts[mk].sum()
        d2 = ((U[:, None, :] - C[None, :, :]) ** 2).sum(2)
        lab = d2.argmin(1)
        centers = [tuple(int(round(x)) for x in c) for c in C]
        assign = [int(lab[uniq[e]]) for e in ends]
    data = bytearray()
    idxd = bytearray()
    for bi in range(bw * bh):
        by, bx = divmod(bi, bw)
        px = a[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4].reshape(16, 4)
        cc = centers[assign[bi]]
        c555 = [cc[0] | (cc[1] << 5) | (cc[2] << 10), cc[3] | (cc[4] << 5) | (cc[5] << 10)]
        c0, c1 = np.array(_c8(c555[0]), float), np.array(_c8(c555[1]), float)
        has_t = trans[bi]
        if has_t:
            pal4 = [c0, c1, (c0 + c1) // 2]
            mode = 1
        else:
            pal4 = [c0, c1, (5 * c0 + 3 * c1) // 8, (3 * c0 + 5 * c1) // 8]
            mode = 3
        P = np.array(pal4)
        d2 = ((px[:, None, :3] - P[None, :, :]) ** 2).sum(2)
        sel = d2.argmin(1)
        if has_t:
            sel = np.where(px[:, 3] < 128, 3, sel)
        for ry in range(4):
            data.append(sum(int(sel[ry * 4 + rx]) << (2 * rx) for rx in range(4)))
        idxd += struct.pack('<H', assign[bi] | (mode << 14))
    pal = []
    for c in centers:
        pal += [c[0] | (c[1] << 5) | (c[2] << 10), c[3] | (c[4] << 5) | (c[5] << 10)]
    return bytes(data), bytes(idxd), pal


def apply_png_to_bytes(data, is_bmd, tex_name, png, pal_name=None, resize=True, log=print):
    """Replace one texture inside .nsbmd/.nsbtx *bytes*. Returns new bytes (same length)."""
    d = bytearray(data)
    n = NSBMD(bytes(d)) if is_bmd else NSBTX(bytes(d))
    tb = n.tex
    if tb is None or tex_name not in tb.tex_by_name:
        raise ImportError_('texture %r is not stored in this file' % tex_name)
    tex = tb.tex_by_name[tex_name]
    im = png if isinstance(png, Image.Image) else Image.open(png)
    im = im.convert('RGBA')
    if im.size != (tex.w, tex.h):
        if not resize:
            raise ImportError_('PNG is %dx%d but the texture is %dx%d' % (im.width, im.height, tex.w, tex.h))
        log('resizing image %dx%d -> %dx%d' % (im.width, im.height, tex.w, tex.h))
        im = im.resize((tex.w, tex.h), Image.LANCZOS)
    pal_slot = None
    if tex.fmt != 7:
        if pal_name is None and is_bmd:
            for m in n.models:
                for mt in m.materials:
                    if mt['tex'] == tex_name and mt['pal']:
                        pal_name = pal_name or mt['pal']
        if pal_name not in tb.pal_slots:
            guess = tex_name + '_pl'
            if guess in tb.pal_slots:
                pal_name = guess
            else:
                pal_name = next(iter(tb.pal_slots), None)
                if pal_name is not None:
                    log('NOTE: texture %s has no palette of its own; its colours go into %s'
                        % (tex_name, pal_name))
        if pal_name is None:
            raise ImportError_('no palette found for this texture')
        pal_slot = tb.pal_slots[pal_name]
        slot_colors = pal_slot[1] // 2
    else:
        slot_colors = 0
    tdata, idxd, pal = encode_texture(tex, im, slot_colors)
    if len(tdata) != tex.nbytes:
        raise ImportError_('internal size mismatch (%d vs %d)' % (len(tdata), tex.nbytes))
    d[tex.file_off:tex.file_off + len(tdata)] = tdata
    if tex.fmt == 5:
        d[tex.idx_off:tex.idx_off + len(idxd)] = idxd
    if pal_slot is not None:
        pal = list(pal)[:slot_colors]
        pal += [0] * (slot_colors - len(pal))
        d[pal_slot[0]:pal_slot[0] + pal_slot[1]] = struct.pack('<%dH' % slot_colors, *pal)
        log('texture %s: palette %s has room for %d colours' % (tex_name, pal_name, slot_colors))
    return bytes(d)


def import_png_texture(path, tex_name, png, pal_name=None, resize=True, log=print):
    """Replace one texture inside an .nsbmd/.nsbtx file with a PNG. Returns new file bytes."""
    return apply_png_to_bytes(open(path, 'rb').read(), path.lower().endswith('.nsbmd'),
                              tex_name, png, pal_name, resize, log)


# ============================================================ OBJ parsing
class ObjData:
    pass


def parse_obj(path):
    o = ObjData()
    o.V, o.VT, o.VN, o.VC = [], [], [], []
    o.objs = OrderedDict()
    o.mtllibs = []
    o.dir = os.path.dirname(os.path.abspath(path))
    cur_obj, cur_mat = 'default', None
    for line in open(path, encoding='utf-8', errors='replace'):
        p = line.split()
        if not p or p[0].startswith('#'):
            continue
        k = p[0]
        try:
            if k == 'v':
                o.V.append((float(p[1]), float(p[2]), float(p[3])))
                o.VC.append((float(p[4]), float(p[5]), float(p[6])) if len(p) >= 7 else None)
            elif k == 'vt':
                o.VT.append((float(p[1]), float(p[2])))
            elif k == 'vn':
                o.VN.append((float(p[1]), float(p[2]), float(p[3])))
            elif k in ('o', 'g'):
                cur_obj = ' '.join(p[1:]) or 'default'
                o.objs.setdefault(cur_obj, [])
            elif k == 'mtllib':
                o.mtllibs.append(' '.join(p[1:]))
            elif k == 'usemtl':
                cur_mat = ' '.join(p[1:])
            elif k == 'f':
                corners = []
                for tok in p[1:]:
                    a = tok.split('/')
                    vi = int(a[0]); vi = vi - 1 if vi > 0 else len(o.V) + vi
                    ti = ni = None
                    if len(a) > 1 and a[1]:
                        t = int(a[1]); ti = t - 1 if t > 0 else len(o.VT) + t
                    if len(a) > 2 and a[2]:
                        t = int(a[2]); ni = t - 1 if t > 0 else len(o.VN) + t
                    corners.append((vi, ti, ni))
                o.objs.setdefault(cur_obj, []).append((cur_mat, corners))
        except (ValueError, IndexError):
            raise ImportError_('cannot parse OBJ line: %s' % line.strip()[:60])
    return o


# ============================================================ display list writing
def pack_dl(cmds):
    """cmds: list of (opcode, [param words]) -> bytes (4 commands per word, params follow)."""
    words = []
    for i in range(0, len(cmds), 4):
        grp = cmds[i:i + 4]
        w = 0
        for j, (op, _) in enumerate(grp):
            w |= op << (8 * j)
        words.append(w)
        for _, pars in grp:
            words += pars
    return struct.pack('<%dI' % len(words), *words) if words else b'\0\0\0\0'


def dl_counts(dl):
    """(vertices, polygons, triangles, quads) of a display list."""
    n = len(dl) // 4
    w = struct.unpack_from('<%dI' % n, dl)
    i = 0
    vcnt = poly = tri = quad = 0
    prim = None
    nv = 0

    def close():
        nonlocal poly, tri, quad, nv
        if prim == 0:
            tri += nv // 3; poly += nv // 3
        elif prim == 1:
            quad += nv // 4; poly += nv // 4
        elif prim == 2 and nv >= 3:
            tri += nv - 2; poly += nv - 2
        elif prim == 3 and nv >= 4:
            quad += (nv - 2) // 2; poly += (nv - 2) // 2
        nv = 0
    while i < n:
        x = w[i]; i += 1
        for sh in (0, 8, 16, 24):
            c = (x >> sh) & 0xFF
            if c not in CMD_PARAMS:
                continue
            i += CMD_PARAMS[c]
            if c == 0x40:
                close(); prim = w[i - 1] & 3
            elif c == 0x41:
                close(); prim = None
            elif c in (0x23, 0x24, 0x25, 0x26, 0x27, 0x28):
                vcnt += 1; nv += 1
    close()
    return vcnt, poly, tri, quad



IMG_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.tga', '.dds')


def _resolve_image(objdir, ref):
    """Find the image a .mtl mentions, even if it moved or lost its folder/extension."""
    words = ref.replace('"', ' ').replace('\\', '/').split()
    if not words:
        return None
    tries = []
    for w in [ref.replace('\\', '/').strip('"'), words[-1], words[0]]:
        tries += [w, os.path.join(objdir, w), os.path.join(objdir, os.path.basename(w))]
    for c in tries:
        if c and os.path.isfile(c):
            return c
    stem = os.path.splitext(os.path.basename(words[-1]))[0].lower()
    names = {os.path.basename(words[-1]).lower()} | {stem + e for e in IMG_EXTS}
    roots = [objdir] + [os.path.join(objdir, d) for d in sorted(os.listdir(objdir))
                        if os.path.isdir(os.path.join(objdir, d))]
    for root in roots:
        try:
            for f in sorted(os.listdir(root)):
                if f.lower() in names and os.path.isfile(os.path.join(root, f)):
                    return os.path.join(root, f)
        except OSError:
            pass
    return None


def parse_mtl(obj, problems=None):
    """material name -> absolute path of its diffuse texture (or None).
    problems, if given, collects (material, reference) for images that could not be found."""
    tex = {}
    for lib in obj.mtllibs:
        mp = os.path.join(obj.dir, lib)
        if not os.path.exists(mp):
            if problems is not None:
                problems.append(('<mtl file>', lib))
            continue
        cur = None
        for line in open(mp, encoding='utf-8', errors='replace'):
            p = line.strip().split(None, 1)
            if not p:
                continue
            if p[0] == 'newmtl' and len(p) > 1:
                cur = p[1].strip(); tex.setdefault(cur, None)
            elif p[0].lower() == 'map_kd' and cur and len(p) > 1:
                found = _resolve_image(obj.dir, p[1].strip())
                if found:
                    tex[cur] = found
                elif problems is not None:
                    problems.append((cur, p[1].strip()))
    return tex


def pick_mtl(mtl, name):
    """Look up a group's texture tolerantly (case, Blender's '.001' suffixes, missing usemtl)."""
    if name in mtl:
        return mtl[name]
    low = (name or '').strip().lower()
    if low:
        for k, v in mtl.items():
            if k.strip().lower() == low:
                return v
        stem = low.split('.')[0]
        for k, v in mtl.items():
            if k.strip().lower().split('.')[0] == stem:
                return v
    with_tex = [v for v in mtl.values() if v]
    if not low and len(with_tex) == 1:            # OBJ without any usemtl line
        return with_tex[0]
    return None


def _quantize_uv(st, info):
    """Shift each triangle's texel coords by whole texture repeats (centred), return (int array, n_clamped)."""
    ps_s, ps_t = info.period() if info is not None else (None, None)
    st = st.copy()
    for ti in range(len(st) // 3):
        for ax, per in ((0, ps_s), (1, ps_t)):
            vals = st[3 * ti:3 * ti + 3, ax]
            if per:
                mid = (vals.min() + vals.max()) / 2.0
                st[3 * ti:3 * ti + 3, ax] = vals - math.floor(mid / per + 0.5 + 1e-6) * per
    sq = np.rint(st * 16.0).astype(np.int64)
    clipped = int((np.abs(sq) > 32767).sum())
    if clipped:
        sq = np.clip(sq, -32768, 32767)
    return sq, clipped


def _make_dl(q, sq, cols, nrms, use_col, use_norm, use_tex):
    """Build an independent-triangles display list from quantised data (N = 3 * triangles)."""
    cmds = [(0x40, [0])]
    last_col = None
    for k in range(len(q)):
        if use_col:
            c = cols[k]
            c555 = (min(31, int(round(c[0] * 31))) | (min(31, int(round(c[1] * 31))) << 5) |
                    (min(31, int(round(c[2] * 31))) << 10))
            if c555 != last_col:
                cmds.append((0x20, [c555])); last_col = c555
        if use_norm:
            nx, ny, nz = (max(-512, min(511, int(round(x * 511)))) & 0x3FF for x in nrms[k])
            cmds.append((0x21, [nx | (ny << 10) | (nz << 20)]))
        if use_tex:
            cmds.append((0x22, [(int(sq[k][0]) & 0xFFFF) | ((int(sq[k][1]) & 0xFFFF) << 16)]))
        cmds.append((0x23, [(int(q[k][0]) & 0xFFFF) | ((int(q[k][1]) & 0xFFFF) << 16), int(q[k][2]) & 0xFFFF]))
    cmds.append((0x41, []))
    return pack_dl(cmds)


def _sbc_spans(d, p):
    """[(base_cmd, start, end)] of the render-command stream starting at p (RET included)."""
    spans = []
    while True:
        c = d[p]
        b = c & 0x1F
        if b == 0x01:
            spans.append((b, p, p + 1)); return spans
        if b in (0x00, 0x0B):
            n = 1
        elif b == 0x02:
            n = 3
        elif b in (0x03, 0x04, 0x05, 0x07, 0x08):
            n = 2
        elif b == 0x06:
            n = 4 + (1 if c & 0x20 else 0) + (1 if c & 0x40 else 0)
        elif b == 0x09:
            n = 3 + 3 * d[p + 2]
        else:
            raise ImportError_('unknown render command 0x%02x' % c)
        spans.append((b, p, p + n)); p += n


def _insert_posscale(d, m):
    """Wrap every MAT/SHP pair in POSSCALE (0x0B ... 0x2B) so a changed position scale takes effect."""
    s0, s1 = m.pos + m.sbc_o, m.pos + m.mat_o
    out = bytearray()
    for b, a, e in _sbc_spans(d, s0):
        if b == 0x04:
            out.append(0x0B)
        out += d[a:e]
        if b == 0x05:
            out.append(0x2B)
    out += b'\0' * ((-len(out)) % 4)
    delta = len(out) - (s1 - s0)
    nd = bytearray(d[:s0]) + out + bytearray(d[s1:])
    struct.pack_into('<I', nd, m.pos, m.size + delta)
    struct.pack_into('<II', nd, m.pos + 8, m.mat_o + delta, m.shp_o + delta)
    struct.pack_into('<I', nd, m.mdl_base + 4, U32(d, m.mdl_base + 4) + delta)
    struct.pack_into('<I', nd, 8, U32(d, 8) + delta)
    for i in range(U16(d, 0x0E)):
        o = U32(d, 0x10 + 4 * i)
        if o > m.mdl_base:
            struct.pack_into('<I', nd, 0x10 + 4 * i, o + delta)
    return bytes(nd)


def _assemble(d, m, new_dls, mat_patches, new_pos_scale=None):
    """Rebuild the shape section (always the last thing in the MDL0 block) and fix all sizes."""
    buf = bytearray(d)
    for pos, mi in mat_patches:
        buf[pos] = mi
    mpos = m.pos
    ss = mpos + m.shp_o
    old_end = mpos + m.size
    sents = parse_dict(d, ss)
    entry_tbl = ss + U16(d, ss + 6) + 4
    esz = U16(d, ss + U16(d, ss + 6))
    dict_len = min(U32(e, 0) for _, e in sents)
    N = len(sents)
    flags, dls = [], []
    for k, (nm, e) in enumerate(sents):
        o = ss + U32(e, 0)
        fl, dlo, dls_ = struct.unpack_from('<III', d, o + 4)
        if k in new_dls:
            fl, blob = new_dls[k]
        else:
            blob = d[o + dlo:o + dlo + dls_]
        flags.append(fl); dls.append(blob)
    sec = bytearray(d[ss:ss + dict_len])
    for k in range(N):
        struct.pack_into('<I', sec, entry_tbl - ss + k * esz, dict_len + 16 * k)
    off = 0
    hdrs = bytearray()
    for k in range(N):
        hdrs += struct.pack('<HHIII', 0, 16, flags[k], (N - k) * 16 + off, len(dls[k]))
        off += len(dls[k])
    sec += hdrs + b''.join(dls)
    delta = len(sec) - (old_end - ss)
    out = bytearray(buf[:ss]) + sec + bytearray(buf[old_end:])
    struct.pack_into('<I', out, mpos, m.size + delta)
    struct.pack_into('<I', out, m.mdl_base + 4, U32(d, m.mdl_base + 4) + delta)
    struct.pack_into('<I', out, 8, U32(d, 8) + delta)
    for i in range(U16(d, 0x0E)):
        o = U32(d, 0x10 + 4 * i)
        if o > m.mdl_base:
            struct.pack_into('<I', out, 0x10 + 4 * i, o + delta)
    if new_pos_scale is not None:
        struct.pack_into('<ii', out, mpos + m.info_off + 8, int(round(new_pos_scale * 4096)),
                         int(round(4096.0 / new_pos_scale)))
    tv = tp = tt = tq = 0
    for k in range(N):
        a_, b_, c_, d_ = dl_counts(dls[k])
        tv += a_; tp += b_; tt += c_; tq += d_
    struct.pack_into('<HHHH', out, mpos + m.info_off + 0x10, min(tv, 65535), min(tp, 65535), min(tt, 65535), min(tq, 65535))
    return bytes(out), delta, tt + 2 * tq


# ============================================================ OBJ -> NSBMD
def _resolve_material(infos, base, name):
    if not name:
        return None
    cands = [name, re.sub(r'\.\d+$', '', name)]
    for c in cands:
        for k, inf in enumerate(infos):
            if inf.mname == c or inf.mat['name'] == c:
                return k
        if c.startswith(base + '_'):
            cc = c[len(base) + 1:]
            for k, inf in enumerate(infos):
                if inf.mat['name'] == cc:
                    return k
    return None


def import_obj(nsbmd_path, obj_path, shared_tex=None, log=print, force=False):
    """Replace shape geometry of an .nsbmd with the contents of an OBJ. Returns (new_bytes, report)."""
    d = open(nsbmd_path, 'rb').read()
    n = NSBMD(d)
    if len(n.models) != 1:
        raise ImportError_('only single-model files are supported')
    m = n.models[0]
    tb = n.tex or shared_tex
    base = os.path.splitext(os.path.basename(nsbmd_path))[0]
    obj = parse_obj(obj_path)
    infos = [MatInfo(base, mt, tb) for mt in m.materials]
    ps = m.pos_scale

    by_shape = defaultdict(list)
    unmatched = []
    for name, faces in obj.objs.items():
        mt = re.search(r'shp(\d+)', name)
        if mt and faces:
            by_shape[int(mt.group(1))] += faces
        elif faces:
            unmatched.append(name)
    for u in unmatched:
        log('WARNING: object %r has no shpNNN prefix - it was ignored (new objects cannot be added yet)' % u)

    draws = shape_draws(m)
    report = dict(changed=[], kept=[], tris=0, unmatched=unmatched, warnings=[])
    new_dls = {}                       # shape idx -> (flag, bytes)
    mat_patches = []                   # (file pos, new material idx)
    all_old = {x['si']: x for x in draws}
    for dr in draws:
        si = dr['si']
        shape = m.shapes[si]
        if si not in by_shape:
            report['kept'].append(si)         # not in the OBJ -> leave the original shape untouched
            continue
        faces = by_shape[si]
        mats = set(f[0] for f in faces if f[0])
        if len(mats) > 1:
            raise ImportError_('object for shape %d uses %d different materials (%s). Split it by material '
                               'in Blender (Edit mode > P > By Material) and keep a shp%03d_ name prefix on one '
                               'piece only.' % (si, len(mats), ', '.join(sorted(mats)), si))
        new_mi = dr['mi']
        if mats:
            r = _resolve_material(infos, base, next(iter(mats)))
            if r is None:
                log('WARNING: shape %d: material %r not found in model, keeping original' % (si, next(iter(mats))))
            else:
                new_mi = r
        info = infos[new_mi] if new_mi < len(infos) else None
        Wm = np.array(dr['W'], dtype=np.float64)
        try:
            Winv = np.linalg.inv(Wm)
        except np.linalg.LinAlgError:
            raise ImportError_('shape %d sits under a non-invertible node transform' % si)
        # original data for colour lookup and "unchanged" detection
        orig = run_display_list(shape['dl'], ps)
        colmap, orig_pts, orig_cols = {}, [], []
        orig_sig = []
        for t in orig:
            sig = []
            for v in t:
                wp = mat_apply(dr['W'], v[:3])
                colmap[(round(wp[0] * 1000), round(wp[1] * 1000), round(wp[2] * 1000))] = v[9]
                orig_pts.append(wp); orig_cols.append(v[9])
                sig.append((round(v[0] / ps * 4096), round(v[1] / ps * 4096), round(v[2] / ps * 4096),
                            round(v[3] * 16), round(v[4] * 16)))
            orig_sig.append(tuple(sorted(sig)))
        orig_pts = np.array(orig_pts) if orig_pts else None
        orig_cols = np.array(orig_cols) if orig_cols else None

        # triangulate (fan)
        tris = []
        for _, corners in faces:
            for k in range(1, len(corners) - 1):
                tris.append((corners[0], corners[k], corners[k + 1]))
        flag = shape['flag']
        use_norm = bool(flag & 1)
        use_col = bool(flag & 2)
        use_tex = bool((flag & 4) or (info is not None and info.tex is not None))
        pts = np.array([obj.V[c[0]] for t in tris for c in t], dtype=np.float64).reshape(-1, 3)
        homog = np.hstack([pts, np.ones((len(pts), 1))])
        loc = (homog @ Winv.T)[:, :3] / ps * 4096.0
        lim = np.abs(loc).max() if len(loc) else 0
        if lim > 32767:
            raise ImportError_('shape %d: geometry is too large for this model (%.1f units of the allowed '
                               '8 x position-scale = %.1f). Scale the object down in Blender.'
                               % (si, lim / 4096.0 * ps, 8 * ps))
        q = np.rint(loc).astype(np.int64)
        st = []
        for t in tris:
            for c in t:
                if c[1] is not None and info is not None and info.tex is not None:
                    u, v = obj.VT[c[1]]
                    st.append(info.st(u, v))
                else:
                    st.append((0.0, 0.0))
        st = np.array(st, dtype=np.float64).reshape(-1, 2)

        # unchanged? keep the original (compact) display list
        new_sig = []
        for ti in range(len(tris)):
            sig = [(int(q[3 * ti + j][0]), int(q[3 * ti + j][1]), int(q[3 * ti + j][2]),
                    round(st[3 * ti + j][0] * 16), round(st[3 * ti + j][1] * 16)) for j in range(3)]
            new_sig.append(tuple(sorted(sig)))
        same_geo = (sorted(new_sig) == sorted(orig_sig)) and not force
        if same_geo:
            report['kept'].append(si)
            if new_mi != dr['mi']:
                mat_patches.append((dr['mat_pos'], new_mi))
            continue

        sq, clipped = _quantize_uv(st, info)
        if clipped:
            log('WARNING: shape %d: %d UV values were outside the DS range and were clamped' % (si, clipped))

        # colours
        cols = []
        for t in tris:
            for c in t:
                col = obj.VC[c[0]]
                if col is None and use_col:
                    p = obj.V[c[0]]
                    key = (round(p[0] * 1000), round(p[1] * 1000), round(p[2] * 1000))
                    col = colmap.get(key)
                    if col is None and orig_pts is not None:
                        dd = ((orig_pts - np.array(p)) ** 2).sum(1)
                        col = tuple(orig_cols[int(dd.argmin())])
                    if col is None:
                        col = (1.0, 1.0, 1.0)
                cols.append(col)
        nrms = None
        if use_norm:
            NT = Winv[:3, :3].T
            nrms = []
            for t in tris:
                for c in t:
                    nv = np.array(obj.VN[c[2]]) if c[2] is not None else np.array([0.0, 1.0, 0.0])
                    nv = NT @ nv
                    nrms.append(nv / (np.linalg.norm(nv) or 1.0))
        blob = _make_dl(q, sq, cols, nrms, use_col, use_norm, use_tex)
        new_flag = (flag & 1) | (flag & 2) | (4 if use_tex else 0)
        new_dls[si] = (new_flag, blob)
        report['changed'].append(si)
        report['tris'] += len(tris)
        if new_mi != dr['mi']:
            mat_patches.append((dr['mat_pos'], new_mi))
        log('shape %d: %d triangles written (was %d)' % (si, len(tris), len(orig)))

    if not new_dls and not mat_patches:
        report['note'] = 'OBJ matches the model - nothing to change'
        return d, report

    if report['changed']:
        cur, bb = 0, set()
        for c in m.sbc:
            if c[0] == 'NODEDESC':
                cur = c[1]
            elif c[0] == 'NODE':
                cur = c[1]
            elif c[0] == 'BB':
                bb.add(cur)
        hit = sorted(set(dr['node'] for dr in shape_draws(m) if dr['si'] in report['changed']) & bb)
        if hit:
            log('WARNING: %d edited shape(s) sit under billboard node(s) %s. The game turns those towards the '
                'camera, so the edited part can appear rotated. "Replace with any OBJ" rebuilds the model and '
                'can switch the billboards off.' % (len(report['changed']), sorted(hit)))

    out, delta, total = _assemble(d, m, new_dls, mat_patches)
    report['total_tris'] = total
    report['size_delta'] = delta
    return out, report


# ============================================================ replace a whole model with any OBJ
def _group_obj(obj):
    groups = OrderedDict()
    for name, faces in obj.objs.items():
        for mat, corners in faces:
            key = mat or ''
            for k in range(1, len(corners) - 1):
                groups.setdefault(key, []).append((corners[0], corners[k], corners[k + 1]))
    return groups


def _usage(m):
    use = defaultdict(int)
    for dr in shape_draws(m):
        use[dr['mi']] += len(run_display_list(m.shapes[dr['si']]['dl'], m.pos_scale))
    return use


def auto_map(groups, infos, base, usage):
    """groups: [{'name','tris','tex'}]  ->  {group name: target material index}"""
    cand = [k for k in range(len(infos)) if usage.get(k, 0) > 0] or list(range(len(infos)))
    auto, used = {}, set()
    for g in groups:                                   # 1. same name as a model material
        r = _resolve_material(infos, base, g['name'])
        if r is not None and r in cand and r not in used:
            auto[g['name']] = r; used.add(r)
    rest = sorted([g for g in groups if g['name'] not in auto], key=lambda g: (g['tex'] is None, -g['tris']))
    pool = sorted([k for k in cand if k not in used], key=lambda k: (infos[k].tex is None, -usage.get(k, 0)))
    fallback = pool[0] if pool else (next(iter(used)) if used else cand[0])
    for g in rest:                                     # 2. largest groups -> biggest textured materials
        auto[g['name']] = pool.pop(0) if pool else fallback
    return auto


def analyze_replace(nsbmd_path, obj_path, shared_tex=None):
    d = open(nsbmd_path, 'rb').read()
    n = NSBMD(d)
    m = n.models[0]
    tb = n.tex or shared_tex
    base = os.path.splitext(os.path.basename(nsbmd_path))[0]
    obj = parse_obj(obj_path)
    mtl = parse_mtl(obj)
    groups = _group_obj(obj)
    infos = [MatInfo(base, mt, tb) for mt in m.materials]
    usage = _usage(m)
    targets = [dict(idx=k, name=inf.mat['name'], tex=inf.tex.name if inf.tex else None,
                    size=(inf.tex.w, inf.tex.h) if inf.tex else None, tris=usage.get(k, 0))
               for k, inf in enumerate(infos)]
    gl = [dict(name=g, tris=len(t), tex=pick_mtl(mtl, g)) for g, t in groups.items()]
    return dict(groups=gl, targets=targets, auto=auto_map(gl, infos, base, usage), slots=len(shape_draws(m)))


def replace_model(nsbmd_path, obj_path, mapping=None, import_textures=True, shared_tex=None,
                  shared_path=None, log=print, disable_billboards=True):
    """
    Put the geometry of ANY OBJ into an existing .nsbmd, ignoring object names.
    Each OBJ material group is attached to one of the model's own materials (mapping, or automatic);
    optionally the group's texture image is imported into that material's texture slot.
    disable_billboards turns the BB (camera-facing) commands of the nodes that receive the new
    geometry into no-ops: the new geometry was written for the node's normal transform, so leaving
    the billboard on makes those parts swing around the camera.
    Returns ({path: new_bytes, ...}, report).
    """
    d0 = open(nsbmd_path, 'rb').read()
    n0 = NSBMD(d0)
    if len(n0.models) != 1:
        raise ImportError_('only single-model files are supported')
    m0 = n0.models[0]
    base = os.path.splitext(os.path.basename(nsbmd_path))[0]
    tb0 = n0.tex or shared_tex
    obj = parse_obj(obj_path)
    mtl_problems = []
    mtl = parse_mtl(obj, mtl_problems)
    groups = _group_obj(obj)
    if not groups:
        raise ImportError_('the OBJ contains no faces')
    infos = [MatInfo(base, mt, tb0) for mt in m0.materials]
    usage = _usage(m0)
    gl = [dict(name=g, tris=len(t), tex=pick_mtl(mtl, g)) for g, t in groups.items()]
    mp = auto_map(gl, infos, base, usage)
    mp.update({k: v for k, v in (mapping or {}).items() if k in groups})
    for g, t in mp.items():
        if not (0 <= t < len(infos)):
            raise ImportError_('bad material mapping for %r' % g)
    report = dict(mapping={g: infos[t].mat['name'] for g, t in mp.items()}, textures=[], warnings=[])

    # ---- textures first (they never change file sizes)
    files = {}
    d = d0
    shared_bytes = None
    report['texture_bank'] = ('the model itself' if n0.tex is not None
                              else (os.path.basename(shared_path) if shared_path else None))
    if import_textures:
        done = set()
        shared_slot, no_slot = 0, []
        for g in sorted(groups, key=lambda g: -len(groups[g])):
            tp, inf = pick_mtl(mtl, g), infos[mp[g]]
            if not tp:
                continue
            if inf.tex is None:
                no_slot.append(inf.mat['name'])
                continue
            if inf.tex.name in done:
                shared_slot += 1
                log('NOTE: texture slot %s is already taken by a bigger group; skipping the texture of %r' % (inf.tex.name, g))
                continue
            im = Image.open(tp)
            log('importing texture %s into slot %s' % (os.path.basename(tp), inf.tex.name))
            if n0.tex is not None:
                d = apply_png_to_bytes(d, True, inf.tex.name, im, inf.mat['pal'], True, log)
            else:
                if not shared_path:
                    raise ImportError_('this model uses a shared .nsbtx but its path was not given')
                if shared_bytes is None:
                    shared_bytes = open(shared_path, 'rb').read()
                shared_bytes = apply_png_to_bytes(shared_bytes, False, inf.tex.name, im, inf.mat['pal'], True, log)
            done.add(inf.tex.name); report['textures'].append((inf.tex.name, os.path.basename(tp)))
        if report['textures']:
            extra = []
            if shared_slot:
                extra.append('%d part(s) share a slot with a bigger part' % shared_slot)
            if no_slot:
                extra.append('%d part(s) went to a material without a texture' % len(no_slot))
            log('textures: %d image(s) imported into %s%s' % (
                len(report['textures']), report['texture_bank'], ' (' + ', '.join(extra) + ')' if extra else ''))
            if shared_path and n0.tex is None:
                log('remember to keep %s (with the new textures) next to the model in the ROM' %
                    os.path.basename(shared_path))
        else:
            why = []
            if not obj.mtllibs:
                why.append('the OBJ has no "mtllib" line, so it does not name any texture')
            elif mtl_problems:
                why.append('these images could not be found: %s' %
                           '; '.join('%s -> %s' % p for p in mtl_problems[:3]))
            elif not any(mtl.values()):
                why.append('its .mtl has no "map_Kd" lines (the OBJ was saved without its textures)')
            if no_slot:
                why.append('none of the chosen target materials has a texture slot')
            log('WARNING: no texture was imported from the OBJ%s' %
                ((': ' + '; '.join(why)) if why else ''))
            report['warnings'].append('no texture imported')

    # ---- geometry
    n = NSBMD(d)
    m = n.models[0]
    draws = shape_draws(m)
    if not draws:
        raise ImportError_('the model has no drawable shapes to reuse')
    mat_tris = OrderedDict()
    for g, tris in groups.items():
        mat_tris.setdefault(mp[g], []).append((g, tris))
    needed = sorted(mat_tris, key=lambda k: -sum(len(t) for _, t in mat_tris[k]))
    if len(needed) > len(draws):                       # more materials than shape slots: merge the smallest
        host = needed[0]
        for k in needed[len(draws):]:
            mat_tris[host] += mat_tris.pop(k)
            report['warnings'].append('model has only %d shape slots: %r merged into material %s'
                                      % (len(draws), infos[k].mat['name'], infos[host].mat['name']))
            log('WARNING: ' + report['warnings'][-1])
        needed = needed[:len(draws)]
    unused = list(range(len(draws)))
    assign = {}
    for tm in needed:                                  # prefer a slot that already draws this material
        for k in unused:
            if draws[k]['mi'] == tm:
                assign[tm] = k; unused.remove(k); break
    for tm in needed:
        if tm not in assign:
            assign[tm] = unused.pop(0)

    slot = {}
    for tm, k in assign.items():
        info = infos[tm]
        pts, st, cols = [], [], []
        for g, tris in mat_tris[tm]:
            for t in tris:
                for c in t:
                    pts.append(obj.V[c[0]])
                    if c[1] is not None and info.tex is not None:
                        st.append(info.st(*obj.VT[c[1]]))
                    else:
                        st.append((0.0, 0.0))
                    cols.append(obj.VC[c[0]] or (1.0, 1.0, 1.0))
        Wm = np.array(draws[k]['W'], dtype=np.float64)
        try:
            Winv = np.linalg.inv(Wm)
        except np.linalg.LinAlgError:
            raise ImportError_('a node transform of this model cannot be inverted')
        P = np.array(pts, dtype=np.float64).reshape(-1, 3)
        loc = (np.hstack([P, np.ones((len(P), 1))]) @ Winv.T)[:, :3]
        slot[k] = dict(tm=tm, info=info, loc=loc, st=np.array(st, dtype=np.float64).reshape(-1, 2), cols=cols)
    maxabs = max(float(np.abs(s['loc']).max()) for s in slot.values())
    ps = m.pos_scale
    has_ps = any(c[0] == 'POSSCALE' for c in m.sbc)       # does the render script apply the position scale?
    new_ps = ps
    while maxabs / new_ps > 7.99:
        new_ps *= 2.0
        if new_ps > 32768:
            raise ImportError_('the model is far too large to fit (largest coordinate %.0f)' % maxabs)
    if has_ps and new_ps == ps:                           # small geometry in a coarse model: refine precision
        while new_ps > 0.25 and maxabs / new_ps < 2.0 and maxabs / (new_ps / 2) <= 7.99:
            new_ps /= 2.0
    if new_ps != ps:
        log('model position scale changed from %g to %g so the geometry fits / keeps its precision' % (ps, new_ps))
        if not has_ps:
            d = _insert_posscale(d, m)
            n = NSBMD(d)
            m = n.models[0]
            draws = shape_draws(m)
    if disable_billboards:
        used_nodes = set(draws[k]['node'] for k in slot)
        cur, offs = 0, []
        for c in m.sbc:
            if c[0] == 'NODEDESC':
                cur = c[1]
            elif c[0] == 'NODE':
                cur = c[1]
            elif c[0] == 'BB' and cur in used_nodes:
                offs.append(c[2])                 # absolute file offset of the BB command
        if offs:
            buf = bytearray(d)
            for o in offs:
                buf[o] = 0                        # BB command -> two NOPs (0x00 is the SBC no-op)
                buf[o + 1] = 0
            d = bytes(buf)
            report['billboards'] = len(offs)
            log('turned off %d billboard node(s): the new geometry is static, so it must not follow the camera'
                % len(offs))
    new_dls, patches, tri_total = {}, [], 0
    for k, dr in enumerate(draws):
        if k not in slot:
            new_dls[dr['si']] = (0, b'\0\0\0\0')
            continue
        s = slot[k]
        q = np.rint(s['loc'] / new_ps * 4096.0).astype(np.int64)
        sq, clipped = _quantize_uv(s['st'], s['info'])
        if clipped:
            log('WARNING: %d UV values were outside the DS range and were clamped' % clipped)
        use_tex = s['info'].tex is not None
        new_dls[dr['si']] = (2 | (4 if use_tex else 0), _make_dl(q, sq, s['cols'], None, True, False, use_tex))
        tri_total += len(q) // 3
        if dr['mi'] != s['tm']:
            patches.append((dr['mat_pos'], s['tm']))
        log('slot %d: material %s, %d triangles' % (k, s['info'].mat['name'], len(q) // 3))
    out, delta, total = _assemble(d, m, new_dls, patches, new_ps if new_ps != ps else None)
    files[nsbmd_path] = out
    if shared_bytes is not None:
        files[shared_path] = shared_bytes
    report.update(size_delta=delta, total_tris=total, slots_used=len(slot), slots=len(draws), pos_scale=new_ps)
    return files, report
