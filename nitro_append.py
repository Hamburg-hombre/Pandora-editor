#!/usr/bin/env python3
"""
nitro_append.py - grow NITRO 3D resource files instead of overwriting slots.

The rest of this project only ever patches bytes *in place* (see
nitro_write.apply_png_to_bytes).  This module adds the missing half: it can
append new named textures/palettes to a TEX0 block (standalone .nsbtx or the
one embedded in a .nsbmd) and append new materials to an NSBMD's MDL0
material section, rebuilding the NITRO "info blocks" (dictionary + metadata)
and fixing up every offset/size along the way.

All functions take and return raw file bytes; the caller decides where to
write them.  No third-party modules are needed beyond the standard library.
"""
import struct

from nitro import NitroError, U8, U16, U32, NSBMD


# --------------------------------------------------------------- info blocks
def load_info_block(d, off, entry_len):
    """Read a NITRO info block.

    Returns [[name, unk00, unk02, entry_bytes], ...].  This is the same layout
    ndspy calls an "info block": an 8-byte overall header, an 8-byte metadata
    block header (magic 0x17F) with one (u16, u16) per entry, a 4-byte entry
    header, the fixed-width entries, then 16-byte NUL-padded names.
    """
    if off + 8 > len(d):
        raise NitroError('info block out of range')
    cnt = d[off + 1]
    tree = U16(d, off + 6)
    p = off + tree
    if p + 4 > len(d):
        raise NitroError('info block entry header out of range')
    esz = U16(d, p)
    if esz != entry_len:
        raise NitroError('info block: expected %d-byte entries, found %d' % (entry_len, esz))
    p += 4
    names_at = p + cnt * esz
    out = []
    for i in range(cnt):
        unk00, unk02 = struct.unpack_from('<HH', d, off + 12 + 4 * i)
        end = names_at + (i + 1) * 16
        if end > len(d):
            raise NitroError('info block name table out of range')
        name = d[names_at + 16 * i:end].split(b'\0')[0].decode('latin1')
        out.append([name, unk00, unk02, bytes(d[p + i * esz:p + (i + 1) * esz])])
    return out


def save_info_block(entries, entry_len):
    """Inverse of load_info_block().  entries: (name, unk00, unk02, entry_bytes)."""
    data = bytearray(struct.pack('<xBH', len(entries), 0))
    data += struct.pack('<HHI', 8, 12 + 4 * len(entries), 0x17F)
    for e in entries:
        data += struct.pack('<HH', e[1], e[2])
    data += struct.pack('<HH', entry_len, 4 + len(entries) * entry_len)
    for e in entries:
        if len(e[3]) != entry_len:
            raise NitroError('info block entry has length %d, expected %d' % (len(e[3]), entry_len))
        data += e[3]
    for e in entries:
        name = e[0] or ''
        data += name.encode('latin1')[:15].ljust(16, b'\0')
    struct.pack_into('<H', data, 2, len(data))
    return bytes(data)


def info_block_end(d, off):
    """Absolute offset just past an info block (uses the full-size field)."""
    return off + U16(d, off + 2)


# --------------------------------------------------------------- TEX0 specs
_FMT_BPP = {1: 8, 2: 2, 3: 4, 4: 8, 5: 2, 6: 8, 7: 16}
_SIZE_CODES = {8: 0, 16: 1, 32: 2, 64: 3, 128: 4, 256: 5, 512: 6, 1024: 7}
_CODE_SIZES = {v: k for k, v in _SIZE_CODES.items()}


def tex_param(fmt, w, h, color0_transp=False, repeat_s=False, repeat_t=False,
              mirror_s=False, mirror_t=False, coord_mode=0):
    """Build the u16 parameter word stored in a texture info-block entry."""
    if w not in _SIZE_CODES or h not in _SIZE_CODES:
        raise NitroError('texture size must be a power of two from 8 to 1024')
    p = 1 if repeat_s else 0
    p |= 2 if repeat_t else 0
    p |= 4 if mirror_s else 0
    p |= 8 if mirror_t else 0
    p |= (_SIZE_CODES[w] << 4) | (_SIZE_CODES[h] << 7)
    p |= (fmt & 7) << 10
    if color0_transp:
        p |= 0x2000
    p |= (coord_mode & 3) << 14
    return p


def _pad8(b):
    return bytes(b) + b'\0' * ((-len(b)) % 8)


def _tex0_from_specs(specs, template_header):
    """Build a complete TEX0 block from texture specs + palette specs.

    spec (texture): name, unk00, unk02, param, unk04, data1, data2
    spec (palette): name, unk00, unk02, unkheader02, data
    template_header: the original 0x3C-byte TEX0 header (unknown fields kept).
    """
    tex_specs, pal_specs = specs
    tdata = bytearray()
    cdata1 = bytearray()
    cdata2 = bytearray()
    pdata = bytearray()
    entries = []
    for t in tex_specs:
        off8 = len(cdata1) if ((t['param'] >> 10) & 7) == 5 else len(tdata)
        if ((t['param'] >> 10) & 7) == 5:
            cdata1 += _pad8(t['data1'])
            cdata2 += _pad8(t['data2'])
        else:
            tdata += _pad8(t['data1'])
        entries.append((t['name'], t['unk00'], t['unk02'],
                        struct.pack('<HHI', off8 >> 3, t['param'], t['unk04'])))
    tex_block = save_info_block(entries, 8)

    entries = []
    for p in pal_specs:
        off8 = len(pdata)
        pdata += _pad8(p['data'])
        entries.append((p['name'], p['unk00'], p['unk02'],
                        struct.pack('<HH', off8 >> 3, p['unkheader02'])))
    pal_block = save_info_block(entries, 4)

    head = bytearray(template_header[:0x38]).ljust(0x3C, b'\0')
    tex_info_off = 0x3C
    pal_info_off = tex_info_off + len(tex_block)
    tex_data_off = pal_info_off + len(pal_block)
    comp1_off = tex_data_off + len(tdata)
    comp2_off = comp1_off + len(cdata1)
    pal_data_off = comp2_off + len(cdata2)
    total = pal_data_off + len(pdata)
    comp_info_off = U16(template_header, 0x1E) if len(template_header) >= 0x20 else tex_info_off
    struct.pack_into('<4sIIHH3IHH4IHH2I', head, 0,
                     b'TEX0', total, U32(template_header, 0x08),
                     len(tdata) >> 3, tex_info_off,
                     U32(template_header, 0x10), tex_data_off,
                     U32(template_header, 0x18), len(cdata1) >> 3, comp_info_off,
                     U32(template_header, 0x20), comp1_off, comp2_off,
                     U32(template_header, 0x2C), len(pdata) >> 3,
                     U16(template_header, 0x32), pal_info_off, pal_data_off)
    return bytes(head) + tex_block + pal_block + bytes(tdata) + bytes(cdata1) + bytes(cdata2) + bytes(pdata)


def _parse_tex0(d, b):
    """Parse a TEX0 block into texture specs, palette specs and its header."""
    if d[b:b + 4] != b'TEX0':
        raise NitroError('no TEX0 at 0x%X' % b)
    header = bytes(d[b:b + 0x3C])
    total = U32(d, b + 4)
    tex_info = U16(d, b + 0x0E)
    tex_data = U32(d, b + 0x14)
    comp1 = U32(d, b + 0x24)
    comp2 = U32(d, b + 0x28)
    pal_info = U32(d, b + 0x34)
    pal_data = U32(d, b + 0x38)
    pal_len = U16(d, b + 0x30) * 8

    tex_specs = []
    for name, u0, u2, e in load_info_block(d, b + tex_info, 8):
        off = U16(e, 0) * 8
        param = U16(e, 2)
        fmt = (param >> 10) & 7
        w = 8 << ((param >> 4) & 7)
        h = 8 << ((param >> 7) & 7)
        d1 = d2 = b''
        if fmt == 5:
            n1, n2 = w * h // 4, w * h // 8
            d1 = bytes(d[b + comp1 + off:b + comp1 + off + n1])
            d2 = bytes(d[b + comp2 + off // 2:b + comp2 + off // 2 + n2])
        else:
            n1 = w * h * _FMT_BPP.get(fmt, 8) // 8
            d1 = bytes(d[b + tex_data + off:b + tex_data + off + n1])
        tex_specs.append(dict(name=name, unk00=u0, unk02=u2, param=param,
                              unk04=U32(e, 4), data1=d1, data2=d2))

    pal_specs = []
    pents = load_info_block(d, b + pal_info, 4)
    starts = sorted(set(U16(e, 0) * 8 for _, _, _, e in pents))
    for name, u0, u2, e in pents:
        o = U16(e, 0) * 8
        nxt = [x for x in starts if x > o]
        end = nxt[0] if nxt else pal_len
        pal_specs.append(dict(name=name, unk00=u0, unk02=u2,
                              unkheader02=U16(e, 2),
                              data=bytes(d[b + pal_data + o:b + pal_data + end])))
    return tex_specs, pal_specs, header, total, b


def replace_tex0(data, new_tex0, tex_off, old_total):
    """Splice a new TEX0 block into a file and fix the NDS block table."""
    out = bytearray(data[:tex_off]) + bytearray(new_tex0) + bytearray(data[tex_off + old_total:])
    delta = len(new_tex0) - old_total
    if delta:
        for i in range(U16(data, 0x0E)):
            o = U32(data, 0x10 + 4 * i)
            if o > tex_off:
                struct.pack_into('<I', out, 0x10 + 4 * i, o + delta)
    struct.pack_into('<I', out, 8, len(out))
    return bytes(out)


def append_textures(data, additions, log=print):
    """Append textures/palettes to an .nsbtx (or any file with a TEX0 block).

    Each addition is a dict with:
      name, fmt, w, h, color0_transp, data1, data2, palette
    plus optional unk00/unk02/unk04/unkheader02 (defaults copied from the
    first existing entry).  Returns (new_bytes, {name: final_texture_name}).
    """
    if data[:4] == b'BTX0':
        tex_off = next((U32(data, 0x10 + 4 * i) for i in range(U16(data, 0x0E))
                        if data[U32(data, 0x10 + 4 * i):U32(data, 0x10 + 4 * i) + 4] == b'TEX0'), None)
    elif data[:4] == b'BMD0':
        tex_off = next((U32(data, 0x10 + 4 * i) for i in range(U16(data, 0x0E))
                        if data[U32(data, 0x10 + 4 * i):U32(data, 0x10 + 4 * i) + 4] == b'TEX0'), None)
    else:
        tex_off = None
    if tex_off is None:
        raise NitroError('no TEX0 block to append to')

    tex_specs, pal_specs, header, old_total, b = _parse_tex0(data, tex_off)
    _nt0, _np0 = len(tex_specs), len(pal_specs)
    existing = {t['name'] for t in tex_specs} | {p['name'] for p in pal_specs}
    tmpl_t = tex_specs[0] if tex_specs else dict(unk00=0, unk02=0, unk04=0)
    tmpl_p = pal_specs[0] if pal_specs else dict(unk00=0, unk02=0, unkheader02=0)

    used = set(existing)
    names = {}
    for a in additions:
        base = ''.join(c if c.isalnum() or c == '_' else '_' for c in a['name'])[:15].strip('_') or 'NewTex'
        name = base
        k = 1
        while name in used:
            name = ('%s%d' % (base[:13], k))[:15]
            k += 1
        used.add(name)
        names[a['name']] = name
        tex_specs.append(dict(
            name=name, unk00=a.get('unk00', tmpl_t['unk00']),
            unk02=a.get('unk02', tmpl_t['unk02']),
            param=tex_param(a['fmt'], a['w'], a['h'], a.get('color0_transp', a['fmt'] in (1, 2, 3, 4, 6))),
            unk04=a.get('unk04', tmpl_t['unk04']),
            data1=a['data1'], data2=a.get('data2', b'')))
        if a['fmt'] != 7:
            pal = list(a['palette'])
            while len(pal) % 4:
                pal.append(0)
            if not pal:
                pal = [0, 0, 0, 0]
            pname = (name[:12] + '_pl')[:15]
            while pname in used:
                pname = (pname[:12] + str(k))[:15]
                k += 1
            used.add(pname)
            pal_specs.append(dict(
                name=pname, unk00=a.get('pal_unk00', tmpl_p['unk00']),
                unk02=a.get('pal_unk02', tmpl_p['unk02']),
                unkheader02=a.get('unkheader02', tmpl_p['unkheader02']),
                data=struct.pack('<%dH' % len(pal), *pal)))
            a['pal_name'] = pname
        else:
            a['pal_name'] = None

    new_tex0 = _tex0_from_specs((tex_specs, pal_specs), header)
    out = replace_tex0(data, new_tex0, tex_off, old_total)
    log('NSBTX: %d -> %d texture(s), %d -> %d palette(s), size %+d bytes'
        % (_nt0, len(tex_specs), _np0, len(pal_specs), len(out) - len(data)))
    return out, names


# --------------------------------------------------------------- NSBMD materials
def append_materials(data, additions, log=print):
    """Append materials to the single model's material section of an .nsbmd.

    Each addition: {name, template (material index), tex_name, pal_name}.
    Returns (new_bytes, [new material indices]).
    """
    n = NSBMD(data)
    if len(n.models) != 1:
        raise NitroError('material appending supports single-model files only')
    m = n.models[0]
    ms = m.pos + m.mat_o
    sec_end = m.pos + m.shp_o
    tex_rel = U16(data, ms)
    pal_rel = U16(data, ms + 2)

    mat_entries = load_info_block(data, ms + 4, 4)
    tex_entries = load_info_block(data, ms + tex_rel, 4)
    pal_entries = load_info_block(data, ms + pal_rel, 4)
    orig_dict_end = info_block_end(data, ms + pal_rel)
    orig_data = bytes(data[orig_dict_end:sec_end])

    # Material data blocks are fixed size for all materials that don't use a
    # texture matrix; derive it from the offsets (they are packed together).
    data_offs = sorted(U32(e, 0) for _, _, _, e in mat_entries)
    step = data_offs[1] - data_offs[0] if len(data_offs) > 1 else 0x2C
    if not (0x20 <= step <= 0x200):
        step = 0x2C

    # Patch every original offset to account for the grown dictionaries, then
    # append one new entry (and its data) per addition.
    new_header = struct.pack('<HH', 0, 0)   # patched once dict sizes are known
    mat_new = list(mat_entries)
    tex_new = list(tex_entries)
    pal_new = list(pal_entries)
    extra = bytearray()
    indices = []

    for add in additions:
        idx = len(mat_new)
        indices.append(idx)
        tmpl = add['template']
        if not (0 <= tmpl < len(mat_entries)):
            raise NitroError('bad template material index %d' % tmpl)
        src_off = U32(mat_entries[tmpl][3], 0)
        mdata = bytes(data[ms + src_off:ms + src_off + step])
        if len(mdata) != step:
            raise NitroError('template material data is truncated')

        mname = ''.join(c if c.isalnum() or c == '_' else '_' for c in add['name'])[:15].strip('_') or ('NewMat%d' % idx)
        used = {e[0] for e in mat_new}
        if mname in used:
            k = 1
            while ('%s%d' % (mname[:12], k)) in used:
                k += 1
            mname = '%s%d' % (mname[:12], k)

        # material entry: u32 offset (relative to the material section)
        mat_new.append([mname, mat_entries[tmpl][1], mat_entries[tmpl][2],
                        struct.pack('<I', 0)])

        # Texture list entry: {u16 index-array offset, u8 count, u8 pad}
        tname = add['tex_name']
        toff = len(orig_data) + len(extra)
        extra += struct.pack('<B', idx)
        tex_new.append([tname[:15], tex_entries[0][1] if tex_entries else 0,
                        tex_entries[0][2] if tex_entries else 0,
                        struct.pack('<HBx', toff, 1)])

        # Palette list entry (only when the texture uses one)
        if add.get('pal_name'):
            poff = len(orig_data) + len(extra)
            extra += struct.pack('<B', idx)
            pal_new.append([add['pal_name'][:15], pal_entries[0][1] if pal_entries else 0,
                            pal_entries[0][2] if pal_entries else 0,
                            struct.pack('<HBx', poff, 1)])

        # Material data appended at the very end
        matdata_off = len(orig_data) + len(extra)
        extra += mdata
        mat_new[-1][3] = struct.pack('<I', matdata_off)

    mat_block = save_info_block(mat_new, 4)
    tex_block = save_info_block(tex_new, 4)
    pal_block = save_info_block(pal_new, 4)
    new_tex_rel = 4 + len(mat_block)
    new_pal_rel = new_tex_rel + len(tex_block)
    new_header = struct.pack('<HH', new_tex_rel, new_pal_rel)
    new_dicts = new_header + mat_block + tex_block + pal_block
    shift = len(new_dicts) - (orig_dict_end - ms)

    # Original entries point into orig_data; move them past the new dicts.
    for e in mat_new[:-len(additions)]:
        e[3] = struct.pack('<I', U32(e[3], 0) + shift)
    ntex_orig = len(tex_entries)
    npal_orig = len(pal_entries)
    for e in tex_new[:ntex_orig]:
        lo, cnt = struct.unpack_from('<HB', e[3], 0)
        e[3] = struct.pack('<HBx', lo + shift, cnt)
    for e in pal_new[:npal_orig]:
        lo, cnt = struct.unpack_from('<HB', e[3], 0)
        e[3] = struct.pack('<HBx', lo + shift, cnt)
    # New entries were expressed relative to the start of orig_data.
    for e in mat_new[-len(additions):]:
        e[3] = struct.pack('<I', U32(e[3], 0) + len(new_dicts))
    for e in tex_new[ntex_orig:]:
        lo, cnt = struct.unpack_from('<HB', e[3], 0)
        e[3] = struct.pack('<HBx', lo + len(new_dicts), cnt)
    for e in pal_new[npal_orig:]:
        lo, cnt = struct.unpack_from('<HB', e[3], 0)
        e[3] = struct.pack('<HBx', lo + len(new_dicts), cnt)

    # Rebuild the info blocks now that offsets are final.
    mat_block = save_info_block(mat_new, 4)
    tex_block = save_info_block(tex_new, 4)
    pal_block = save_info_block(pal_new, 4)
    new_header = struct.pack('<HH', len(mat_block) + 4, len(mat_block) + 4 + len(tex_block))
    new_dicts = new_header + mat_block + tex_block + pal_block
    new_section = new_dicts + orig_data + bytes(extra)
    delta = len(new_section) - (sec_end - ms)

    out = bytearray(data[:ms]) + bytearray(new_section) + bytearray(data[sec_end:])
    struct.pack_into('<I', out, m.pos, m.size + delta)
    struct.pack_into('<I', out, m.pos + 0x0C, m.shp_o + delta)   # shapes moved
    struct.pack_into('<I', out, m.mdl_base + 4, U32(data, m.mdl_base + 4) + delta)
    struct.pack_into('<I', out, 8, U32(data, 8) + delta)
    for i in range(U16(data, 0x0E)):
        o = U32(data, 0x10 + 4 * i)
        if o > m.mdl_base:
            struct.pack_into('<I', out, 0x10 + 4 * i, o + delta)
    i = m.pos + m.info_off
    out[i + 4] = min(255, U8(data, i + 4) + len(additions))       # num materials
    log('NSBMD: %d material(s) appended (+%d bytes)' % (len(additions), delta))
    return bytes(out), indices
