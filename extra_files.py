#!/usr/bin/env python3
"""
Extra GoldenEye: Rogue Agent (DS) file types for the Pandora editor.

These formats are not 3D models, so the model viewer cannot show them.  This
module knows how to:

  .char  read/write the 32x32, 4bpp character/title graphic
  .plt   read/write the 16-colour BGR555 palette that goes with a .char
  .ico   read/write the 64x64, 8bpp game-icon portrait
  .crt   summarise the object template table
  .nsbca summarise the NITRO skeletal animation
  .nslbc summarise the level script bytecode
  .tex   show basic information (the layout is not decoded yet)
  .lvl   plain text (the GUI shows it in the Text tab)

Everything that writes goes through the GUI, which keeps a copy in _backup
before touching the file.
"""
import os
import re
import struct

from PIL import Image

import objects_lp as lpmod
import nitro_anim as anim
import menu_tex as mtex
import nslbc_lib as nsdislib
import nslbc_dis as nsdis


EXTENSIONS = ('.char', '.plt', '.ico', '.crt', '.nsbca', '.nsbma', '.nsbta',
              '.nsbtp', '.nslbc', '.tex', '.lvl')

# The GUI's Data tab is used for these (all but .lvl, which is text).
DATA_EXTENSIONS = ('.char', '.plt', '.ico', '.crt', '.nsbca', '.nsbma',
                   '.nsbta', '.nsbtp', '.nslbc', '.tex')


class ExtraError(Exception):
    pass


def is_data_file(path):
    low = path.lower()
    return low.endswith(DATA_EXTENSIONS) or os.path.basename(low) == 'credits.bin'


# ====================================================== colour helpers
def bgr555_to_rgb(c):
    """Nintendo DS BGR555 -> (r, g, b)."""
    r = (c & 0x1f) * 255 // 31
    g = ((c >> 5) & 0x1f) * 255 // 31
    b = ((c >> 10) & 0x1f) * 255 // 31
    return r, g, b


def rgb_to_bgr555(rgb):
    r, g, b = rgb[:3]
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    return ((r * 31 + 127) // 255) | (((g * 31 + 127) // 255) << 5) \
        | (((b * 31 + 127) // 255) << 10)


def index_to_rgba(index, colors, alpha_zero=False):
    if index >= len(colors):
        return 255, 0, 255, 255            # magenta = missing palette entry
    r, g, b = colors[index]
    return r, g, b, 0 if (alpha_zero and index == 0) else 255


def palette_hex(colors):
    return ['#%02X%02X%02X' % tuple(c[:3]) for c in colors]


# ====================================================== palette (.plt)
def decode_plt(data):
    """Return the 16 colours in a .plt file as a list of (r, g, b)."""
    if len(data) < 32:
        raise ExtraError('a .plt file must be at least 32 bytes (it has 16 colours)')
    return [bgr555_to_rgb(c) for c in struct.unpack_from('<16H', data, 0)[:16]]


def encode_plt(colors):
    """Build a 32-byte .plt file from up to 16 (r, g, b) colours."""
    out = [rgb_to_bgr555(c) for c in colors[:16]]
    out += [0] * (16 - len(out))
    return struct.pack('<16H', *out)


# ====================================================== tile helpers
def _tile_order(w, h):
    """Yield (x, y, pixel_number) in the DS 8x8 tile order used by .char/.ico."""
    tw, th = w // 8, h // 8
    for ty in range(th):
        for tx in range(tw):
            for y in range(8):
                for x in range(8):
                    yield tx * 8 + x, ty * 8 + y, (ty * tw + tx) * 64 + y * 8 + x


def _encode_tiles(pixels, w, h, bpp):
    """pixels[y*w+x] is a palette index.  Returns tile-ordered bytes."""
    out = bytearray(w * h * bpp // 8)
    for x, y, n in _tile_order(w, h):
        v = pixels[y * w + x] & ((1 << bpp) - 1)
        if bpp == 8:
            out[n] = v
        elif n & 1:
            out[n >> 1] |= v
        else:
            out[n >> 1] = v << 4
    return bytes(out)


# ====================================================== .char
def decode_char(data, colors):
    """Decode a .char (32x32, 4bpp) with a list of 16 palette colours."""
    if len(data) < 512:
        raise ExtraError('a .char file must be at least 512 bytes (32x32, 4bpp)')
    im = Image.new('RGBA', (32, 32))
    px = im.load()
    for x, y, n in _tile_order(32, 32):
        b = data[n >> 1]
        idx = (b >> 4) if (n & 1) == 0 else (b & 15)
        px[x, y] = index_to_rgba(idx, colors, alpha_zero=True)
    return im


def quantize(im, count):
    """Reduce an image to at most `count` colours. Returns (index list, colours, alpha)."""
    im = im.convert('RGBA')
    alpha = im.getchannel('A')
    flat = Image.new('RGB', im.size, (0, 0, 0))
    flat.paste(im.convert('RGB'), (0, 0), im)
    used = flat.getcolors(maxcolors=1 << 24)
    if used and len(used) <= count:
        # Few enough colours: keep them exactly (important for decoded art).
        ordered = [c for _n, c in sorted(used, key=lambda p: (-p[0], p[1]))]
        cmap = {c: i for i, c in enumerate(ordered)}
        idx = [cmap[p] for p in flat.getdata()]
        ordered += [(0, 0, 0)] * (count - len(ordered))
        return idx, ordered, list(alpha.getdata())
    q = flat.quantize(colors=max(1, count), method=Image.MEDIANCUT,
                      dither=Image.Dither.NONE)
    raw = q.getpalette() or []
    colors = []
    for i in range(count):
        if i * 3 + 2 < len(raw):
            colors.append(tuple(raw[i * 3:i * 3 + 3]))
        else:
            colors.append((0, 0, 0))
    idx = list(q.getdata())
    return idx, colors, list(alpha.getdata())


def encode_char(im, transparent=True):
    """
    Convert any picture to .char bytes plus its palette.

    Index 0 is kept transparent (the usual convention for .char graphics) and
    the visible part of the picture uses the other 15 colours.
    Returns (char_bytes, colors16).
    """
    im = im.convert('RGBA').resize((32, 32), Image.NEAREST)
    idx, colors, alpha = quantize(im, 15 if transparent else 16)
    if transparent:
        shift = [(0 if a < 128 else min(15, i + 1)) for i, a in zip(idx, alpha)]
        colors = [(0, 0, 0)] + colors[:15]
        idx = shift
    else:
        colors = colors[:16]
    return _encode_tiles(idx, 32, 32, 4), colors


# ====================================================== .ico
def decode_ico(data):
    """Decode the DS .ico portrait (256-colour palette + 64x64, 8bpp)."""
    need = 512 + 4096
    if len(data) < need:
        raise ExtraError('a .ico file must be at least %d bytes (256 colours + 64x64 pixels)'
                         % need)
    colors = [bgr555_to_rgb(c) for c in struct.unpack_from('<256H', data, 0)]
    im = Image.new('RGBA', (64, 64))
    px = im.load()
    for x, y, n in _tile_order(64, 64):
        px[x, y] = index_to_rgba(data[512 + n], colors)
    return im, colors


def encode_ico(im):
    """Convert any picture to the 4608-byte DS .ico layout."""
    im = im.convert('RGBA').resize((64, 64), Image.NEAREST)
    idx, colors, _alpha = quantize(im, 256)
    out = bytearray()
    out += struct.pack('<256H', *[rgb_to_bgr555(c) for c in colors[:256]])
    out += _encode_tiles(idx, 64, 64, 8)
    return bytes(out[:4608])


# ====================================================== PNG import/export
def decode_image_pixels(png_path, size, count, transparent_zero):
    im = Image.open(png_path)
    im = im.convert('RGBA').resize((size, size), Image.NEAREST)
    idx, colors, alpha = quantize(im, count - 1 if transparent_zero else count)
    if transparent_zero:
        idx = [(0 if a < 128 else min(count - 1, i + 1)) for i, a in zip(idx, alpha)]
        colors = [(0, 0, 0)] + colors[:count - 1]
    else:
        colors = colors[:count]
    return idx, colors


def write_plt(path, colors):
    with open(path, 'wb') as f:
        f.write(encode_plt(colors))


def _companion(path, ext):
    """Find path with the same base name but another extension (case-insensitive)."""
    base = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.dirname(path)
    try:
        for name in os.listdir(folder):
            if name.lower() == (base + ext).lower():
                return os.path.join(folder, name)
    except OSError:
        pass
    return None


def import_png(path, png_path):
    """
    Import a PNG into .char, .plt or .ico.

    Returns {'written': [(path, description), ...], 'note': str}.
    The caller is responsible for making backups.
    """
    low = path.lower()
    written = []
    note = ''
    if low.endswith('.char'):
        idx, colors = decode_image_pixels(png_path, 32, 16, True)
        data = _encode_tiles(idx, 32, 32, 4)
        with open(path, 'wb') as f:
            f.write(data)
        written.append((path, '32x32 graphic'))
        plt = _companion(path, '.plt')
        if plt:
            write_plt(plt, colors)
            written.append((plt, '16-colour palette'))
        else:
            note = ('No matching .plt was found, so only the graphic was written. The game keeps '
                    'using its own palette, so colours may look different than in your PNG.')
    elif low.endswith('.plt'):
        _idx, colors = decode_image_pixels(png_path, 32, 16, False)
        write_plt(path, colors)
        written.append((path, '16-colour palette'))
        note = 'The picture itself is unchanged; only the 16 palette colours were replaced.'
    elif low.endswith('.ico'):
        with open(path, 'wb') as f:
            f.write(encode_ico(Image.open(png_path)))
        written.append((path, '64x64, 256-colour portrait'))
    else:
        raise ExtraError('PNG import is only available for .char, .plt and .ico files')
    return {'written': written, 'note': note}


# ====================================================== .crt
def _printable_strings(data, start=0, min_len=3):
    out = []
    for m in re.finditer(rb'[ -~]{%d,}\x00' % min_len, data[start:]):
        out.append((start + m.start(), m.group()[:-1].decode('latin-1')))
    return out


def _find_lvl(path):
    folder = os.path.dirname(path)
    for entry in (os.path.join(folder, 'Global.LVL'),
                  os.path.join(os.path.dirname(folder), 'Global.LVL')):
        if os.path.exists(entry):
            return entry
    try:
        for entry in os.listdir(folder):
            if entry.lower() == 'global.lvl':
                return os.path.join(folder, entry)
    except OSError:
        pass
    return None


def describe_crt(path, data):
    words = list(struct.unpack_from('<8I', data, 0)) if len(data) >= 32 else []
    entries = []
    table_end = len(data)
    # The names start at the first known template name; the table is before it.
    m = re.search(rb'Barrel\x00', data)
    if m:
        table_end = m.start()
    off = 0x60
    while off + 16 <= table_end:
        h, kind, zero, index = struct.unpack_from('<IIII', data, off)
        if zero == 0 and kind in (9, 0x0b):
            entries.append((h, kind, index))
        off += 16
    objects = [e for e in entries if e[1] == 9]
    ai = [e for e in entries if e[1] == 0x0b]
    names = []
    if m:
        names = _printable_strings(data, m.start(), 3)
    try:
        details = lpmod.load_template_details(path, _find_lvl(path))
    except Exception:
        details = []
    obj_names = [d['name'] for d in details if d['kind'] == 'object']
    ai_names = [d['name'] for d in details if d['kind'] == 'ai']

    lines = []
    lines.append('Object template table (Templates.CRT)')
    lines.append('')
    lines.append('Size:      %s bytes' % format(len(data), ','))
    if words:
        lines.append('Header:    ' + '  '.join('0x%X' % w for w in words))
        lines.append('           (first 8 words; version-like value: %d)' % words[0])
    lines.append('Table:     starts at 0x60; entries are 16 bytes')
    lines.append('           { name hash, kind (9=object, 11=AI), 0, index }')
    lines.append('Objects:   %d template(s)' % len(objects))
    lines.append('AI:        %d template(s)' % len(ai))
    line = ''
    lines.append('')
    if obj_names:
        lines.append('Object names:')
        for name in obj_names[:40]:
            lines.append('  ' + name)
        if len(obj_names) > 40:
            lines.append('  ... and %d more' % (len(obj_names) - 40))
    elif names:
        lines.append('Readable names:')
        for _off, name in names[:40]:
            lines.append('  ' + name)
    if ai_names:
        lines.append('')
        lines.append('AI names (from Global.LVL):')
        for name in ai_names[:30]:
            lines.append('  ' + name)
    lines += ['', 'The table can be edited byte by byte with the patch box below.',
              'Keep a name hash in sync with any string you rename in Global.LVL.']
    return '\n'.join(lines)


# ====================================================== NITRO animations
# Parsers live in nitro_anim.py and are validated on all 286 animation files.
def _anim_common(data, parser, label):
    try:
        r = parser(data)
    except Exception as e:
        return ['%s' % label, '', 'Could not parse: %s: %s' % (type(e).__name__, e)]
    h = r['header']
    lines = [label, '']
    lines.append('Size:      %d bytes (header says %d)' % (len(data), h['size']))
    lines.append('Container: %s v%d, chunk at 0x%X, animation at 0x%X'
                 % (h['magic'].decode('latin-1'), h['version'], r['chunk'],
                    r['anims'][0]['off'] if r['anims'] else 0))
    lines.append('Animation(s): %d' % len(r['anims']))
    for a in r['anims'][:3]:
        if 'frames' in a:
            lines.append('  %s: %d frames, %d tracks'
                         % (a['name'], a['frames'], len(a.get('tracks', []))))
        else:
            lines.append('  %s: %d texture(s), %d palette(s), %d tracks'
                         % (a['name'], len(a.get('texnames', [])),
                            len(a.get('palnames', [])), len(a.get('tracks', []))))
    if len(r['anims']) > 3:
        lines.append('  ... and %d more' % (len(r['anims']) - 3))
    return lines, r


def _fmt_curve(c):
    if c['const']:
        if c['kind'] == 'rot':
            return 'const rot %d (%s)' % (c['matrix'], 'pivot' if c['pivot'] else 'basis')
        return 'const %.4f' % c['value']
    s = c.get('samples', [])
    if not s:
        return 'sampled %d..%d (no samples)' % (c['start'], c['end'])
    first = float(s[0].get('value', s[0].get('rot_idx', 0)))
    last = float(s[-1].get('value', s[-1].get('rot_idx', 0)))
    return 'sampled %d..%d, %d keys: %.3f .. %.3f' % (
        c['start'], c['end'], len(s), first, last)


def describe_nsbca(path, data):
    out = _anim_common(data, anim.parse_nsbca, 'NITRO skeletal animation (.nsbca)')
    if isinstance(out, list):
        return '\n'.join(out)
    lines, r = out
    for a in r['anims'][:1]:
        lines += ['', 'Tracks (node index = animated joint):',
                  '  #  offset  flags   node  channels']
        for i, t in enumerate(a['tracks'][:50]):
            ch = '; '.join('%s %s' % (nm, _fmt_curve(c)) for nm, c in t['channels'])
            lines.append('  %2d 0x%04X 0x%04X %4d  %s'
                         % (i, t['off'], t['flags'], t['target'], ch[:110]))
        if len(a['tracks']) > 50:
            lines.append('  ... and %d more tracks' % (len(a['tracks']) - 50))
        m = a['matrices']
        lines.append('')
        lines.append('Rotation matrices: %d pivot, %d basis (referenced)'
                     % (len(m['pivot']), len(m['basis'])))
    lines += ['', 'Byte-level edits are possible with the patch box; the viewer does not play animations.']
    return '\n'.join(lines)


def describe_nsbta(path, data):
    out = _anim_common(data, anim.parse_nsbta, 'NITRO texture/UV animation (.nsbta)')
    if isinstance(out, list):
        return '\n'.join(out)
    lines, r = out
    names = ('scale U', 'scale V', 'rotation', 'translate U', 'translate V')
    for a in r['anims'][:2]:
        lines += ['', 'Tracks (material name -> 5 UV channels):']
        for t in a['tracks'][:40]:
            lines.append('  %-22s %s' % (t['name'][:22],
                         ', '.join('%s=f%04X(%d)' % (names[i], c['flags'], c['nframes'])
                                   for i, c in enumerate(t['channels']))))
        if len(a['tracks']) > 40:
            lines.append('  ... and %d more tracks' % (len(a['tracks']) - 40))
    return '\n'.join(lines)


def describe_nsbtp(path, data):
    out = _anim_common(data, anim.parse_nsbtp, 'NITRO texture-pattern animation (.nsbtp)')
    if isinstance(out, list):
        return '\n'.join(out)
    lines, r = out
    for a in r['anims'][:2]:
        lines.append('')
        lines.append('Textures: %s' % ', '.join(a['texnames'][:10]))
        lines.append('Palettes: %s' % ', '.join(a['palnames'][:10]))
        lines.append('Flipbook tracks:')
        for t in a['tracks'][:30]:
            ks = ', '.join('%d:%s/%s' % (k['frame'], k['tex'], k['pal']) for k in t['keys'][:8])
            lines.append('  %-20s %d keys: %s' % (t['name'][:20], t['nkeys'], ks))
    return '\n'.join(lines)


def describe_nsbma(path, data):
    out = _anim_common(data, anim.parse_nsbma, 'NITRO material animation (.nsbma)')
    if isinstance(out, list):
        return '\n'.join(out)
    lines, r = out
    for a in r['anims'][:2]:
        lines += ['', 'Tracks (material name -> 5 channels):',
                  '  material                ch0     ch1     ch2     ch3     ch4']
        for t in a['tracks'][:40]:
            cells = []
            for c in t['channels']:
                if c['flags'] & 0x20:
                    cells.append('fix %04X' % c['fixed'])
                else:
                    cells.append('%d keys' % len(c.get('values', [])))
            lines.append('  %-22s %s' % (t['name'][:22], ' '.join('%-8s' % x for x in cells)))
        if len(a['tracks']) > 40:
            lines.append('  ... and %d more tracks' % (len(a['tracks']) - 40))
    lines += ['', 'Channel-to-material-property mapping is inferred; values are decoded from the file.']
    return '\n'.join(lines)


# ====================================================== .nslbc
def describe_nslbc(path, data):
    """Level script bytecode: section tables + disassembled callback blocks."""
    lines = ['Level script bytecode (NSLBC)', '']
    lines.append('Size:      %d bytes' % len(data))
    try:
        r = nsdislib.parse(data)
        s = r['s']
        h = s['hdr']
    except Exception as e:
        return lines + ['', 'Could not parse: %s: %s' % (type(e).__name__, e)]
    if h[0] != 3 or s['str'] + h[6] != len(data):
        return lines + ['', 'Unexpected header (version %d, strings end at 0x%X, file %d bytes)'
                        % (h[0], s['str'] + h[6], len(data))]
    lines.append('Header:    version %d, code %d bytes, strings %d bytes'
                 % (h[0], h[5], h[6]))
    lines.append('Sections:  A %d records @0x%X, B %d @0x%X, C %d @0x%X, D %d @0x%X'
                 % (h[1], s['a'], h[2], s['b'], h[3], s['c'], h[4], s['d']))
    lines.append('Strings:   %d, code instructions: %d words'
                 % (len(r['strs']), len(r['code']) // 4))
    lines += ['', 'Callback blocks (C): hash -> code offset/length',
              '  #   callback                     hash      offset  length']
    for i, (ch, off, ln, fl) in enumerate(r['C'][:40]):
        nm = nsdis.HASHNAMES.get(ch, '')
        lines.append('  %-3d %-28s %08X %7d %7d' % (i, nm[:28], ch, off, ln))
    if len(r['C']) > 40:
        lines.append('  ... and %d more' % (len(r['C']) - 40))
    # disassemble up to 8 callback blocks
    work = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'work'))
    lvl = os.path.basename(os.path.dirname(path))
    try:
        rev = nsdis.load_names(work, lvl)
        strmap = {o - s['str']: o2 for o, o2 in r['strs']}
        for i, (ch, off, ln, fl) in enumerate(r['C'][:8]):
            lines += ['', '=== C#%d %s ===' % (i, rev.get(ch, '%08X' % ch))]
            if ln % 4 or off + ln > len(r['code']):
                lines.append('  (invalid block)')
                continue
            w = struct.unpack_from('<%dI' % (ln // 4), r['code'], off)
            j = 0
            shown = 0
            while j < len(w) and shown < 30:
                op = w[j]
                n = nsdis.ARITY.get(op)
                if n is None:
                    lines.append('  %04x: UNKNOWN %08X' % (off + 4 * j, op))
                    break
                args = w[j + 1:j + 1 + n]
                j += 1 + n
                a = ', '.join(nsdis.operand_annotation(x, strmap, rev, op) for x in args)
                lines.append('  %04x: %-8s %s' % (off + 4 * (j - 1 - n),
                                                  nsdis.OPNAME.get(op, 'OP%04x' % op), a))
                shown += 1
            if j < len(w):
                lines.append('  ... (%d more instruction(s))' % (len(w) - j))
    except Exception as e:
        lines.append('')
        lines.append('Disassembly skipped: %s: %s' % (type(e).__name__, e))
    lines += ['', 'Full disassembly: python nslbc_dis.py <work> <level>  (see NSLBC_Format.md).',
              'Byte edits with the patch box stay valid; do not reorder sections or change',
              'the code/string size fields without fixing the callback offsets.']
    return '\n'.join(lines)


# ====================================================== credits.bin
def describe_credits(path, data):
    """Menus/credits.bin: {u32 count, u32 offset[count], NUL strings}.
    Offsets are relative to 4 + 4*count (the start of the strings region)."""
    lines = ['Credits string table (credits.bin)', '']
    if len(data) < 4:
        return lines + ['File is too short.']
    n = struct.unpack_from('<I', data, 0)[0]
    base = 4 + 4 * n
    lines.append('Size:      %d bytes' % len(data))
    lines.append('Strings:   %d, table at 0x%X..0x%X, text from 0x%X'
                 % (n, 4, base - 1, base))
    if base > len(data):
        return lines + ['Offset table runs past the end of the file.']
    offs = list(struct.unpack_from('<%dI' % n, data, 4))
    strings = []
    for i, o in enumerate(offs):
        s = base + o
        if s >= len(data):
            lines.append('Bad offset for string %d: %d' % (i, o))
            break
        e = data.find(b'\x00', s)
        if e < 0:
            lines.append('String %d is not NUL terminated' % i)
            break
        strings.append(data[s:e].decode('latin-1'))
    lines.append('Decoded:   %d/%d string(s)' % (len(strings), n))
    lines += ['', 'First strings:']
    for i, s in enumerate(strings[:40]):
        lines.append('  %3d  %s' % (i, s))
    if len(strings) > 40:
        lines.append('  ... and %d more' % (len(strings) - 40))
    return '\n'.join(lines)


# ====================================================== .tex
def describe_tex(path, data):
    """Menus/menu.tex: 122 LZSS blocks, each a palettised 8-bpp image."""
    lines = ['Menu texture bank (.tex)', '']
    lines.append('Size:      %d bytes' % len(data))
    try:
        bs = list(mtex.blocks(path))
    except Exception as e:
        return lines + ['', 'Could not decode: %s: %s' % (type(e).__name__, e)]
    lines.append('Blocks:    %d LZSS-compressed images' % len(bs))
    lines.append('')
    lines.append('  #  offset     compressed  raw bytes  pad  palette  indices  guessed size')
    for b in bs[:60]:
        img = mtex.parse_image(b['payload'])
        ni = len(img[1]) if img else 0
        w, h = mtex.guess_width(ni) if img else (None, None)
        lines.append(' %3d 0x%07X %9d %10d %3d  %-7s %8s  %s' % (
            b['index'], b['offset'], b['next'] - b['offset'], b['usize'], b['padding'],
            'yes' if img else 'NO', ni,
            '%dx%d' % (w, h) if w else 'unknown'))
    if len(bs) > 60:
        lines.append('  ... and %d more blocks' % (len(bs) - 60))
    lines += ['', 'Block header: u8 0x10 + u24 uncompressed size; payload is LZSS',
              '(MSB-first flags, backrefs: count=(a>>4)+3, distance=((a&0xF)<<8|b)+1).',
              'Each payload is u16[256] RGB555 palette + 8x8-tiled 8-bpp indices.',
              'Widths are not stored in the file; the guessed sizes are inferred.',
              'Use "Export PNGs..." in the toolbar to write every block as an image.',
              'The hex patch box still edits raw bytes.']
    return '\n'.join(lines)


# ====================================================== credits.bin
def describe_credits(path, data):
    """Menus/credits.bin: {u32 count, u32 offset[count], NUL strings}.
    Offsets are relative to 4 + 4*count (the start of the strings region)."""
    lines = ['Credits string table (credits.bin)', '']
    if len(data) < 4:
        return lines + ['File is too short.']
    n = struct.unpack_from('<I', data, 0)[0]
    base = 4 + 4 * n
    lines.append('Size:      %d bytes' % len(data))
    lines.append('Strings:   %d, table at 0x%X..0x%X, text from 0x%X'
                 % (n, 4, base - 1, base))
    if base > len(data):
        return lines + ['Offset table runs past the end of the file.']
    offs = list(struct.unpack_from('<%dI' % n, data, 4))
    strings = []
    for i, o in enumerate(offs):
        s = base + o
        if s >= len(data):
            lines.append('Bad offset for string %d: %d' % (i, o))
            break
        e = data.find(b'\x00', s)
        if e < 0:
            lines.append('String %d is not NUL terminated' % i)
            break
        strings.append(data[s:e].decode('latin-1'))
    lines.append('Decoded:   %d/%d string(s)' % (len(strings), n))
    lines += ['', 'First strings:']
    for i, s in enumerate(strings[:40]):
        lines.append('  %3d  %s' % (i, s))
    if len(strings) > 40:
        lines.append('  ... and %d more' % (len(strings) - 40))
    return '\n'.join(lines)


# ====================================================== .lvl
def lvl_sections(text):
    return re.findall(r'BEGIN\s+"([^"]+)"', text)


# ====================================================== one file object
class ExtraFile:
    def __init__(self, path):
        self.path = path
        self.size = os.path.getsize(path)
        self.kind = 'info'              # 'image' or 'info'
        self.summary = ''
        self.image = None
        self.import_kind = None         # 'char', 'plt', 'ico' or None
        self.palette = None
        self.palette_editable = False
        self.companion = None
        self.preview_note = ''
        self.char_raw = None            # for live .plt previews
        self.load()

    def load(self):
        with open(self.path, 'rb') as f:
            data = f.read()
        low = self.path.lower()
        if low.endswith('.char'):
            plt = _companion(self.path, '.plt')
            self.companion = plt
            colors = decode_plt(open(plt, 'rb').read()) if plt else [(i * 17,) * 3 for i in range(16)]
            self.image = decode_char(data, colors)
            self.kind = 'image'
            self.import_kind = 'char'
            lines = ['Character / title graphic (.char)', '']
            lines.append('Size:      %d bytes' % len(data))
            lines.append('Shape:     32 x 32 pixels, 4 bits per pixel')
            lines.append('Storage:   8x8 tile order, 512 bytes')
            if plt:
                lines.append('Palette:   %s (16 colours)' % os.path.basename(plt))
            else:
                lines.append('Palette:   no matching .plt found (preview uses greys)')
            lines += ['', 'Import PNG: resizes the picture to 32x32, reduces it to',
                      '15 colours (index 0 stays transparent) and rewrites the',
                      'matching .plt palette too. A backup is kept.']
            self.summary = '\n'.join(lines)
            self.preview_note = '32 x 32 pixels, 4 bits per pixel (index 0 is transparent)'
        elif low.endswith('.plt'):
            colors = decode_plt(data)
            char = _companion(self.path, '.char')
            self.companion = char
            self.palette = colors
            self.palette_editable = True
            self.kind = 'image'
            self.import_kind = 'plt'
            if char:
                self.char_raw = open(char, 'rb').read()
                self.image = decode_char(self.char_raw, colors)
                preview = 'uses %s' % os.path.basename(char)
            else:
                self.image = palette_strip(colors)
                preview = 'palette only (no matching .char)'
            lines = ['Palette file (.plt)', '']
            lines.append('Size:      %d bytes' % len(data))
            lines.append('Entries:   16 colours, Nintendo DS BGR555')
            lines.append('Preview:   %s' % preview)
            lines.append('')
            lines.append('Colours:   ' + ' '.join(palette_hex(colors)))
            lines += ['', 'Click a colour chip above to pick a new colour, then',
                      '"Apply palette and save". Import PNG takes the 16 main',
                      'colours from a picture instead. A backup is kept.']
            self.summary = '\n'.join(lines)
            self.preview_note = '16 palette colours, preview ' + preview
        elif low.endswith('.ico'):
            self.image, colors = decode_ico(data)
            self.kind = 'image'
            self.import_kind = 'ico'
            lines = ['Game-icon portrait (.ico)', '']
            lines.append('Size:      %d bytes (expected 4608)' % len(data))
            lines.append('Palette:   256 colours at 0x000 (512 bytes, BGR555)')
            lines.append('Image:     64x64 pixels at 0x200 (4096 bytes, 8bpp)')
            lines.append('Storage:   8x8 tile order')
            if len(data) > 4608:
                lines.append('Extra:     %d trailing byte(s) are preserved' % (len(data) - 4608))
            lines += ['', 'Import PNG resizes the picture to 64x64, reduces it to',
                      '256 colours and rewrites both the palette and the pixels.',
                      'A backup is kept.']
            self.summary = '\n'.join(lines)
            self.preview_note = '64 x 64 pixels, 8 bits per pixel'
        elif low.endswith('.crt'):
            self.summary = describe_crt(self.path, data)
        elif low.endswith('.nsbca'):
            self.summary = describe_nsbca(self.path, data)
        elif low.endswith('.nsbma'):
            self.summary = describe_nsbma(self.path, data)
        elif low.endswith('.nsbta'):
            self.summary = describe_nsbta(self.path, data)
        elif low.endswith('.nsbtp'):
            self.summary = describe_nsbtp(self.path, data)
        elif low.endswith('.nslbc'):
            self.summary = describe_nslbc(self.path, data)
        elif low.endswith('.tex'):
            self.summary = describe_tex(self.path, data)
        elif os.path.basename(low) == 'credits.bin':
            self.summary = describe_credits(self.path, data)
        else:
            raise ExtraError('not a supported extra file type')


def palette_strip(colors, cell=32):
    im = Image.new('RGB', (cell * 16, cell))
    px = im.load()
    for i, c in enumerate(colors[:16]):
        for y in range(cell):
            for x in range(cell):
                px[i * cell + x, y] = tuple(c[:3])
    return im


def open_extra(path):
    if not is_data_file(path):
        raise ExtraError('not a supported extra file type')
    return ExtraFile(path)


# ====================================================== hex helpers
HEX_PAGE = 8192        # bytes shown at once (512 lines)


def hex_lines(data, start, length=HEX_PAGE):
    end = min(len(data), start + length)
    lines = []
    for off in range(start, end, 16):
        chunk = data[off:off + 16]
        text = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append('%08X  %-47s  |%s|' % (off, ' '.join('%02X' % b for b in chunk), text))
    return lines


def parse_offset(text, size):
    text = (text or '').strip().lower()
    if text.startswith('0x'):
        text = text[2:]
    try:
        value = int(text, 16)
    except ValueError:
        raise ExtraError('the offset must be a hexadecimal number, for example 0x200')
    if value < 0 or value > size:
        raise ExtraError('the offset must be between 0 and 0x%X' % size)
    return value


def parse_hex_bytes(text):
    raw = re.sub(r'0x', '', text or '', flags=re.I)
    raw = re.sub(r'[^0-9A-Fa-f]', '', raw)
    if not raw:
        raise ExtraError('type the bytes to write, for example: 00 FF 7A')
    if len(raw) & 1:
        raise ExtraError('each byte needs two hex digits (for example 0A, not A)')
    return bytes.fromhex(raw)


def apply_patch(path, offset, blob):
    with open(path, 'r+b') as f:
        f.seek(offset)
        f.write(blob)


def _format_blob(blob):
    return ' '.join('%02X' % b for b in blob)
