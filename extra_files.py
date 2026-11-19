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


EXTENSIONS = ('.char', '.plt', '.ico', '.crt', '.nsbca', '.nslbc', '.tex', '.lvl')

# The GUI's Data tab is used for these (all but .lvl, which is text).
DATA_EXTENSIONS = ('.char', '.plt', '.ico', '.crt', '.nsbca', '.nslbc', '.tex')


class ExtraError(Exception):
    pass


def is_data_file(path):
    return path.lower().endswith(DATA_EXTENSIONS)


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


# ====================================================== .nsbca
def _nsbca_strings(data, start, end):
    out = []
    for m in re.finditer(rb'[ -~]{3,}\x00', data[start:end]):
        out.append(m.group()[:-1].decode('latin-1'))
    return out


def describe_nsbca(path, data):
    if len(data) < 0x20 or data[:4] != b'BCA0':
        raise ExtraError('not a NITRO BCA0 animation')
    filesize = struct.unpack_from('<I', data, 8)[0]
    hdr_size, nchunks = struct.unpack_from('<HH', data, 0x0c)
    chunk = struct.unpack_from('<I', data, 0x10)[0]
    lines = ['NITRO skeletal animation (.nsbca)', '']
    lines.append('Size:      %s bytes (header says %s)' % (format(len(data), ','),
                                                           format(filesize, ',')))
    lines.append('Header:    version %d, %d chunk(s), header size 0x%X'
                 % (struct.unpack_from('<H', data, 6)[0], nchunks, hdr_size))
    if chunk + 8 > len(data) or data[chunk:chunk + 4] != b'JNT0':
        lines.append('')
        lines.append('This file has no JNT0 joint block, so there is no animation to summarise.')
        return '\n'.join(lines)
    jnt0_size = struct.unpack_from('<I', data, chunk + 4)[0]
    jnt0_end = min(len(data), chunk + jnt0_size)
    jac = data.find(b'J\x00AC', chunk + 8, jnt0_end)
    names = _nsbca_strings(data, chunk + 8, jac if jac > 0 else jnt0_end)
    lines.append('JNT0:      offset 0x%X, %s bytes' % (chunk, format(jnt0_size, ',')))
    if names:
        lines.append('Name(s):   ' + ', '.join(names[:6]) + (' ...' if len(names) > 6 else ''))
    if jac < 0:
        lines.append('')
        lines.append('No J.AC animation block was found.')
        return '\n'.join(lines)
    frames, tracks = struct.unpack_from('<HH', data, jac + 4)
    pivot, basis = struct.unpack_from('<II', data, jac + 0x0c)
    lines.append('Animation: offset 0x%X' % jac)
    lines.append('Frames:    %d' % frames)
    lines.append('Tracks:    %d (one per animated joint)' % tracks)
    lines.append('Pivot:     ' + ('none' if pivot == 0 else '0x%X' % pivot) +
                 '    Basis: ' + ('none' if basis == 0 else '0x%X' % basis))
    lines.append('')
    lines.append('Track  Offset   Size   Flags   Target  Channels')
    offs = []
    for i in range(tracks):
        if jac + 0x14 + i * 2 + 2 <= len(data):
            offs.append(struct.unpack_from('<H', data, jac + 0x14 + i * 2)[0])
    for i, rel in enumerate(offs[:60]):
        off = jac + rel
        if off + 4 > len(data):
            continue
        nxt = jac + offs[i + 1] if i + 1 < len(offs) else min(len(data), jac + (pivot or 0))
        size = max(4, nxt - off)
        flags, _dummy, target = struct.unpack_from('<HBB', data, off)
        # GBATEK: bit0 = no channels, bits1-2 / 6-7 / 9-10 hide a whole channel,
        # bits3-5 and 8 and 11-13 mark individual channels as constant.
        channels = []
        if flags & 1:
            channels.append('none')
        else:
            if not (flags & 0b110):
                const = (flags & 0b111000) == 0b111000
                channels.append('translation' + (' (const)' if const else ''))
            if not (flags & 0b11000000):
                channels.append('rotation' + (' (const)' if flags & 0b100000000 else ''))
            if not (flags & 0b11000000000):
                const = (flags & 0b11100000000000) == 0b11100000000000
                channels.append('scale' + (' (const)' if const else ''))
        lines.append('%4d   0x%04X  %5d   0x%04X  %3d     %s'
                     % (i, off, size, flags, target, ', '.join(channels) or 'no channels'))
    if len(offs) > 60:
        lines.append('... and %d more tracks' % (len(offs) - 60))
    lines += ['', 'This is the NITRO animation data. Byte-level tweaks are possible with the',
              'patch box below; the model viewer does not play these animations.']
    return '\n'.join(lines)


# ====================================================== .nslbc
def describe_nslbc(path, data):
    words = list(struct.unpack_from('<8I', data, 0)) if len(data) >= 32 else []
    lines = ['Level script bytecode (NSLBC)', '']
    lines.append('Size:      %s bytes' % format(len(data), ','))
    if words:
        lines.append('Header:    ' + '  '.join('%d (0x%X)' % (w, w) for w in words))
    records = []
    off = 0x20
    while off + 16 <= len(data):
        h, kind, marker, tail = struct.unpack_from('<IIII', data, off)
        if marker != 0xffffffff or tail != 0 or kind > 0x100:
            break
        records.append((off, h, kind))
        off += 16
    lines.append('Hash list: %d record(s) from 0x20' % len(records))
    lines.append('           { name hash, type, 0xFFFFFFFF, 0 }')
    if records:
        kinds = sorted({r[2] for r in records})
        lines.append('Types:     ' + ', '.join(str(k) for k in kinds[:20]) +
                     (' ...' if len(kinds) > 20 else ''))
        lines.append('First:     ' + ', '.join('0x%08X (type %d)' % (h, k)
                                               for _o, h, k in records[:5]))
    # Bytecode bytes often form short random printable runs. Keep only text
    # that looks like a real name or message (mostly letters, normal punctuation).
    strings = []
    for o, s in _printable_strings(data, 0, 4):
        if len(s) < 5 or s != s.strip():
            continue
        if not re.fullmatch(r"[A-Za-z0-9 _\-/.,:!%'()]+", s):
            continue
        if sum(ch.isalpha() for ch in s) < 3:
            continue
        strings.append((o, s))
    lines.append('')
    lines.append('Readable text (%d string(s)):' % len(strings))
    for o, s in strings[:60]:
        lines.append('  0x%05X  %s' % (o, s))
    if len(strings) > 60:
        lines.append('  ... and %d more' % (len(strings) - 60))
    lines += ['', 'The bytecode itself is not decoded. Use the patch box below to change',
              'individual bytes; the strings above are the easiest safe edit.']
    return '\n'.join(lines)


# ====================================================== .tex
def describe_tex(path, data):
    import collections
    lines = ['Texture/data bank (.tex)', '']
    lines.append('Size:      %s bytes' % format(len(data), ','))
    lines.append('First 32:  ' + data[:32].hex(' '))
    counts = collections.Counter(data)
    lines.append('Common bytes: ' + ', '.join('%02X x%s' % (b, format(n, ','))
                                               for b, n in counts.most_common(6)))
    lines.append('')
    lines.append('This file does not have a documented layout in this tool yet:')
    lines.append('it is not a single NITRO texture and it has no readable header.')
    lines.append('It can still be inspected and changed byte by byte with the')
    lines.append('hex view and patch box below. Keep a backup before experimenting.')
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
        elif low.endswith('.nslbc'):
            self.summary = describe_nslbc(self.path, data)
        elif low.endswith('.tex'):
            self.summary = describe_tex(self.path, data)
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
