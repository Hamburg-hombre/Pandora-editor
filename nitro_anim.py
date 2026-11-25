"""Decoders for NITRO animation containers: BCA0/BTA0/BTP0/BMA0.

All offsets are absolute file offsets unless stated otherwise.  Offsets stored
in the files are relative to the containing structure (documented in comments).
"""
import struct

U8 = lambda d, o: d[o]
U16 = lambda d, o: struct.unpack_from('<H', d, o)[0]
S16 = lambda d, o: struct.unpack_from('<h', d, o)[0]
U32 = lambda d, o: struct.unpack_from('<I', d, o)[0]

MAGICS = {b'BCA0': 'JNT0', b'BTA0': 'SRT0', b'BTP0': 'PAT0', b'BMA0': 'MAT0'}


class ParseError(Exception):
    pass


def name_of(raw):
    return raw.split(b'\0', 1)[0].decode('latin-1')


def parse_header(d):
    if len(d) < 0x14:
        raise ParseError('file too small')
    magic = d[:4]
    if magic not in MAGICS:
        raise ParseError('bad container magic %r' % magic)
    bom, ver = struct.unpack_from('<HH', d, 4)
    size = U32(d, 8)
    hdr_size, nchunks = struct.unpack_from('<HH', d, 0x0C)
    offs = [U32(d, 0x10 + 4 * i) for i in range(nchunks)]
    return dict(magic=magic, bom=bom, version=ver, size=size,
                hdr_size=hdr_size, nchunks=nchunks, chunk_offs=offs)


def parse_dict(d, off, base=None):
    """Parse a NITRO Dict / NameList. Returns list of (name, data_bytes, data_off).

    Offset 0x00 dummy u8, 0x01 count u8, 0x02 total_size u16,
    0x04 tree_offset u16 (==8), 0x06 data_offset u16,
    0x08 tree root + (tree_offset/4-2) patricia entries (4 bytes each),
    then siz u16, data_section_size u16, count*siz data bytes, count*16 names.
    """
    if off + 8 > len(d):
        raise ParseError('dict header out of range at 0x%X' % off)
    cnt = d[off + 1]
    total = U16(d, off + 2)
    tree_off = U16(d, off + 4)
    data_off = U16(d, off + 6)
    if tree_off != 8 or data_off < 0x10:
        raise ParseError('dict layout unexpected at 0x%X (tree=%X data=%X)'
                         % (off, tree_off, data_off))
    ntree = (data_off - tree_off) // 4 - 1
    tree_root = U32(d, off + 8)
    tree = [U32(d, off + 0x0C + 4 * i) for i in range(ntree)]
    siz = U16(d, off + data_off)
    dsec_size = U16(d, off + data_off + 2)
    data_at = off + data_off + 4
    names_at = data_at + cnt * siz
    if names_at + cnt * 16 > len(d):
        raise ParseError('dict entries past EOF at 0x%X' % off)
    out = []
    for i in range(cnt):
        out.append((name_of(d[names_at + 16 * i: names_at + 16 * (i + 1)]),
                    d[data_at + i * siz: data_at + (i + 1) * siz],
                    data_at + i * siz))
    return dict(entries=out, off=off, total=total, count=cnt, tree_root=tree_root,
                tree=tree, siz=siz, data_sec=dsec_size, data_at=data_at,
                names_at=names_at)


def parse_container(d):
    h = parse_header(d)
    chunk = h['chunk_offs'][0]
    if d[chunk:chunk + 4] != MAGICS[h['magic']].encode():
        raise ParseError('expected chunk %s at 0x%X, found %r'
                         % (MAGICS[h['magic']], chunk, d[chunk:chunk + 4]))
    chunk_size = U32(d, chunk + 4)
    top = parse_dict(d, chunk + 8)
    return h, chunk, chunk_size, top


# --------------------------------------------------------------------- NSBCA
def f12(v):
    """signed 1.19.12 fixed-point -> float"""
    v &= 0xFFFFFFFF
    if v & 0x80000000:
        v -= 0x100000000
    return v / 4096.0


def f_s12(v):
    """signed 1.3.12 fixed-point -> float"""
    v &= 0xFFFF
    if v & 0x8000:
        v -= 0x10000
    return v / 4096.0


def curve(d, p, jac, kind, const):
    """Parse one Curve at absolute offset p. Returns dict."""
    if const:
        if kind == 'rot':
            idx = U16(d, p)
            return dict(kind=kind, const=True, rot_idx=idx,
                        pivot=bool(idx & 0x8000), matrix=idx & 0x7FFF,
                        off=p, size=4)
        v = U32(d, p)
        c = dict(kind=kind, const=True, value=f12(v), raw=v, off=p,
                 size=4)
        if kind == 'scale':
            v2 = U32(d, p + 4)
            c['unknown'] = f12(v2)
            c['unknown_raw'] = v2
            c['size'] = 8
        return c
    info = U32(d, p)
    start = info & 0xFFFF
    if (info >> 16) & 0x800:
        end = ((info >> 16) & 0xFFF) - 0x1000  # unlikely, keep signed
    else:
        end = (info >> 16) & 0xFFF
    width = (info >> 28) & 3
    rate = 1 << ((info >> 30) & 3)
    samples_off = U32(d, p + 4)
    nsamp = (end - start) // rate if end >= start else 0
    # width != 0 means 16-bit samples in practice (observed width values:
    # 0 = 32-bit, 2 = 16-bit).  Rotation is always u16 matrix indices.
    samp_size = 4 if width == 0 else 2
    if kind == 'rot':
        samp_size = 2
    elif kind == 'scale':
        samp_size *= 2
    c = dict(kind=kind, const=False, start=start, end=end, width=width,
             rate=rate, samples_off=samples_off, samples_at=jac + samples_off,
             nsamp=nsamp, samp_size=samp_size, off=p, size=8)
    samples = []
    sp = jac + samples_off
    for i in range(nsamp):
        q = sp + i * samp_size
        if q + samp_size > len(d):
            c['truncated'] = True
            break
        if kind == 'rot':
            idx = U16(d, q)
            samples.append(dict(rot_idx=idx, pivot=bool(idx & 0x8000),
                                matrix=idx & 0x7FFF))
        elif width != 0:
            samples.append(dict(value=f_s12(U16(d, q))))
            if kind == 'scale':
                samples[-1]['unknown'] = f_s12(U16(d, q + 2))
        else:
            samples.append(dict(value=f12(U32(d, q))))
            if kind == 'scale':
                samples[-1]['unknown'] = f12(U32(d, q + 4))
    c['samples'] = samples
    return c


def decode_pivot(raw):
    flags, a, b = struct.unpack_from('<HHH', raw, 0)
    form = flags & 0xF
    neg_i = bool(flags & 0x10)
    neg_c = bool(flags & 0x20)
    neg_d = bool(flags & 0x40)
    return dict(form=form, neg_i=neg_i, neg_c=neg_c, neg_d=neg_d,
                a=f_s12(a), b=f_s12(b))


def decode_basis(raw):
    xs = struct.unpack_from('<HHHHH', raw, 0)
    ys = [xs[4], xs[0], xs[1], xs[2], xs[3]]
    zs = [0] * 6
    for i in range(5):
        zs[i] = (ys[i] >> 3) & 0x1FFF
        zs[5] = ((zs[5] << 3) | (ys[i] & 7)) & 0xFFFFFFFF
    def s13(v):
        if v & 0x1000:
            v -= 0x2000
        return v / 4096.0
    A = [s13(zs[1]), s13(zs[2]), s13(zs[3])]
    B = [s13(zs[4]), s13(zs[0]), s13(zs[5])]
    C = [A[1] * B[2] - A[2] * B[1],
         A[2] * B[0] - A[0] * B[2],
         A[0] * B[1] - A[1] * B[0]]
    return dict(xs=xs, A=A, B=B, C=C)


def parse_nsbca(d):
    h, chunk, csize, top = parse_container(d)
    res = dict(header=h, chunk=chunk, chunk_size=csize, dict=top, anims=[])
    for name, entry, data_at in top['entries']:
        rel = U32(entry, 0)
        jac = chunk + rel
        if d[jac:jac + 4] != b'J\0AC':
            raise ParseError('bad J.AC magic at 0x%X: %r' % (jac, d[jac:jac+4]))
        frames = U16(d, jac + 4)
        ntracks = U16(d, jac + 6)
        unknown = U32(d, jac + 8)
        pivot_off = U32(d, jac + 0x0C)
        basis_off = U32(d, jac + 0x10)
        tloffs = [U16(d, jac + 0x14 + 2 * i) for i in range(ntracks)]
        anim = dict(name=name, off=jac, frames=frames, ntracks=ntracks,
                    unknown=unknown, pivot_off=pivot_off, basis_off=basis_off,
                    track_offs=tloffs, tracks=[])
        # rotation matrices are shared: pivot 6 bytes, basis 10 bytes each
        npiv = nbasis = 0
        for t in tloffs:
            tp = jac + t
            flags = U16(d, tp)
            dummy = d[tp + 2]
            target = d[tp + 3]
            p = tp + 4
            chans = []
            tx = ty = tz = rot = sx = sy = sz = None
            if not flags & 1:
                if not flags & 0b110:
                    for nm, bit in (('tx', 3), ('ty', 4), ('tz', 5)):
                        c = curve(d, p, jac, 'trans', bool(flags & (1 << bit)))
                        chans.append((nm, c))
                        if nm == 'tx': tx = c
                        if nm == 'ty': ty = c
                        if nm == 'tz': tz = c
                        p += c['size']
                if not flags & 0b11000000:
                    rot = curve(d, p, jac, 'rot', bool(flags & 0x100))
                    chans.append(('rot', rot))
                    p += rot['size']
                if not flags & 0b11000000000:
                    for nm, bit in (('sx', 11), ('sy', 12), ('sz', 13)):
                        c = curve(d, p, jac, 'scale', bool(flags & (1 << bit)))
                        chans.append((nm, c))
                        if nm == 'sx': sx = c
                        if nm == 'sy': sy = c
                        if nm == 'sz': sz = c
                        p += c['size']
            anim['tracks'].append(dict(off=tp, flags=flags, dummy=dummy,
                                       target=target, size=p - tp,
                                       channels=chans))
        # Collect rotation matrix references. The arrays themselves are stored
        # at 6 bytes (pivot) / 10 bytes (basis) per entry; only referenced
        # matrices are decoded, since the array lengths aren't stored.
        piv_idx, bas_idx = set(), set()
        for t in anim['tracks']:
            for nm, c in t['channels']:
                if c['kind'] != 'rot':
                    continue
                if c['const']:
                    refs = [c]
                else:
                    refs = c.get('samples', [])
                for s in refs:
                    idx = s.get('rot_idx', 0)
                    (piv_idx if idx & 0x8000 else bas_idx).add(idx & 0x7FFF)
        anim['matrices'] = dict(pivot=[], basis=[], pivot_indices=sorted(piv_idx),
                                basis_indices=sorted(bas_idx))
        for i in sorted(piv_idx):
            q = jac + pivot_off + 6 * i
            if q + 6 <= len(d):
                anim['matrices']['pivot'].append((i, decode_pivot(d[q:q+6])))
        for i in sorted(bas_idx):
            q = jac + basis_off + 10 * i
            if q + 10 <= len(d):
                anim['matrices']['basis'].append((i, decode_basis(d[q:q+10])))
        res['anims'].append(anim)
    return res


# --------------------------------------------------------------------- NSBTA
def parse_srt_channel(d, p, anim_off):
    nframes = U16(d, p)
    flags = U16(d, p + 2)
    field = U32(d, p + 4)
    ch = dict(off=p, nframes=nframes, flags=flags, field=field)
    if flags == 0x1000:
        ch['samples_at'] = anim_off + field
        ch['samples'] = [f_s12(U16(d, anim_off + field + 2 * i))
                         for i in range(nframes)
                         if anim_off + field + 2 * i + 2 <= len(d)]
    return ch


def parse_nsbta(d):
    h, chunk, csize, top = parse_container(d)
    res = dict(header=h, chunk=chunk, chunk_size=csize, dict=top, anims=[])
    for name, entry, data_at in top['entries']:
        rel = U32(entry, 0)
        aoff = chunk + rel
        if d[aoff:aoff + 4] != b'M\0AT':
            raise ParseError('bad M.AT magic at 0x%X: %r' % (aoff, d[aoff:aoff+4]))
        frames = U16(d, aoff + 4)
        unk = U16(d, aoff + 6)
        tracks = parse_dict(d, aoff + 8)
        anim = dict(name=name, off=aoff, frames=frames, unknown=unk,
                    tracks=[])
        for tname, tentry, tdata_at in tracks['entries']:
            if tracks['siz'] != 0x28:
                raise ParseError('SRT track size %X != 28' % tracks['siz'])
            chans = [parse_srt_channel(d, tdata_at + 8 * i, aoff) for i in range(5)]
            anim['tracks'].append(dict(name=tname, off=tdata_at, channels=chans))
        res['anims'].append(anim)
    return res


# --------------------------------------------------------------------- NSBTP
def parse_nsbtp(d):
    h, chunk, csize, top = parse_container(d)
    res = dict(header=h, chunk=chunk, chunk_size=csize, dict=top, anims=[])
    for name, entry, data_at in top['entries']:
        rel = U32(entry, 0)
        aoff = chunk + rel
        if d[aoff:aoff + 4] != b'M\0PT':
            raise ParseError('bad M.PT magic at 0x%X: %r' % (aoff, d[aoff:aoff+4]))
        unknown = U16(d, aoff + 4)
        ntex, npal = d[aoff + 6], d[aoff + 7]
        texoff, paloff = U16(d, aoff + 8), U16(d, aoff + 0x0A)
        tracks = parse_dict(d, aoff + 0x0C)
        texnames = [name_of(d[aoff + texoff + 16 * i:aoff + texoff + 16 * (i + 1)])
                    for i in range(ntex)]
        palnames = [name_of(d[aoff + paloff + 16 * i:aoff + paloff + 16 * (i + 1)])
                    for i in range(npal)]
        anim = dict(name=name, off=aoff, unknown=unknown,
                    texnames=texnames, palnames=palnames,
                    texoff=texoff, paloff=paloff, tracks=[])
        for tname, tentry, tdata_at in tracks['entries']:
            nkeys = U32(tentry, 0)
            flags = U16(tentry, 4)
            koff = U16(tentry, 6)
            keys = []
            for i in range(nkeys):
                q = aoff + koff + 4 * i
                if q + 4 > len(d):
                    break
                keys.append(dict(frame=U16(d, q), tex=d[q + 2], pal=d[q + 3]))
            anim['tracks'].append(dict(name=tname, off=tdata_at, nkeys=nkeys,
                                       flags=flags, key_off=koff, keys=keys))
        res['anims'].append(anim)
    return res


# --------------------------------------------------------------------- NSBMA
def parse_nsbma(d):
    h, chunk, csize, top = parse_container(d)
    res = dict(header=h, chunk=chunk, chunk_size=csize, dict=top, anims=[])
    for name, entry, data_at in top['entries']:
        rel = U32(entry, 0)
        aoff = chunk + rel
        if d[aoff:aoff + 4] != b'M\0AM':
            raise ParseError('bad M.AM magic at 0x%X: %r' % (aoff, d[aoff:aoff+4]))
        frames = U16(d, aoff + 4)
        unknown = U16(d, aoff + 6)
        tracks = parse_dict(d, aoff + 8)
        anim = dict(name=name, off=aoff, frames=frames, unknown=unknown,
                    tracks=[])
        if tracks['siz'] != 0x14:
            raise ParseError('M.AM track size %X != 14' % tracks['siz'])
        for tname, tentry, tdata_at in tracks['entries']:
            chans = []
            for i in range(5):
                off = U16(tentry, 4 * i + 0)
                nkeys = d[tdata_at + 4 * i + 2]
                flags = d[tdata_at + 4 * i + 3]
                ch = dict(index=i, off=off, at=aoff + off, nkeys=nkeys,
                          flags=flags)
                if flags & 0x20:
                    ch['fixed'] = off  # raw 16-bit fixed value
                else:
                    vals = []
                    for j in range(nkeys):
                        q = aoff + off + 2 * j
                        if q + 2 <= len(d):
                            vals.append(U16(d, q))
                    ch['values'] = vals
                chans.append(ch)
            anim['tracks'].append(dict(name=tname, off=tdata_at, channels=chans))
        res['anims'].append(anim)
    return res


PARSERS = dict(nsbca=parse_nsbca, nsbta=parse_nsbta, nsbtp=parse_nsbtp,
               nsbma=parse_nsbma)
