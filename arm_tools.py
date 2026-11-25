#!/usr/bin/env python3
"""ARM9 / ARM7 binaries: decompress, disassemble, patch and write back.

The retail ROM has no overlays.  ARM9 is stored at ROM 0x4000 (load address
0x02000000): a boot stub, then a backward-LZSS compressed body ending in an
8-byte footer.  The footer at file offset 0x6F264 is::

    u32 (high byte 0x0B = 11-byte gap, low 24 bits = compressed length incl. gap)
    u32 decompressed length (0x0004FFAC)

The boot stub (0x02000914) reads the footer, sets the source start to
``footerEnd - compressedLength`` and the destination end to
``footerEnd + decompressedLength``, then decodes downwards.  ARM7 is raw code.

This module exposes:
    load(work)              -> ArmImage objects for both CPUs
    disassemble(image)      -> labelled ARM text listing
    patch / save            -> edit the readable image and rebuild the .bin
    repack_with_arm(...)    -> write a new .nds with edited ARM binaries

CLI:
    python arm_tools.py info   <work folder>
    python arm_tools.py dis    <work folder> [arm9|arm7] [out.txt]
    python arm_tools.py test   <work folder>          # verify LZSS round-trip
"""
import os
import re
import struct
import sys

try:
    import capstone
except ImportError:                       # disassembly is optional; patching is not
    capstone = None

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ARM9_STUB = 0x4000                 # bytes at the start of _arm9.bin, loaded at 0x02000000
ARM9_FOOTER_SIZE = 11              # bytes between stream end and footer end
ARM9_GAP = 3                       # padding between stream and 8-byte footer
FOOTER_LEN = 8
ARM9_BASE = 0x02000000
ARM9_BODY_BASE = 0x0206F26C
ARM7_BASE = 0x02380000


class ArmError(Exception):
    pass


# ------------------------------------------------------------------ LZSS codec
def lzss_decompress(comp, start, footer_end, out_size):
    """Decode the backward-LZSS stream.  Returns (bytes, bytes_left, final_ptr)."""
    p = footer_end - ARM9_FOOTER_SIZE
    w = out_size
    out = bytearray(out_size)
    flags, bits = 0, 0
    while p > start and w > 0:
        if bits <= 0:
            flags = comp[p - 1]
            p -= 1
            bits = 8
        bits -= 1
        if flags & 0x80:                       # back-reference
            b1 = comp[p - 1]; p -= 1
            b2 = comp[p - 1]; p -= 1
            off = (((b1 << 8) | b2) & ~0xF000) + 2
            ip = b1 + 0x30
            while ip >= 0x10 and w > 0:
                w -= 1
                out[w] = out[w + off] if w + off < len(out) else 0
                ip -= 0x10
        else:                                  # literal
            w -= 1
            out[w] = comp[p - 1]
            p -= 1
        flags = (flags << 1) & 0xFF
    return bytes(out), w, p


def lzss_compress(data, chain_depth=32):
    """Encode `data` for the backward decoder; returns the stream bytes."""
    n = len(data)
    head, chain = {}, {}
    tokens = []
    w = n - 1
    while w >= 0:
        key = data[w:w + 3]
        best_len, best_off = 0, 0
        cand = head.get(key)
        seen = 0
        while cand is not None and seen < chain_depth:
            seen += 1
            off = cand - w
            if 2 <= off <= 4097:
                ln = 0
                # decoder copies with decreasing indices: output w-ln comes
                # from (w-ln)+off = cand-ln.
                while (ln < 18 and w - ln >= 0 and cand - ln >= 0
                       and data[cand - ln] == data[w - ln]):
                    ln += 1
                if ln > best_len:
                    best_len, best_off = ln, off
                    if ln == 18:
                        break
            cand = chain.get(cand)
        if best_len >= 3:
            val = ((best_len - 3) << 4) | (((best_off - 2) >> 8) & 0x0F)
            b2 = (best_off - 2) & 0xFF
            tokens.append(('R', val, b2))
            for q in range(w, w - best_len, -1):
                k = data[q:q + 3]
                if len(k) == 3:
                    chain[q] = head.get(k)
                    head[k] = q
            w -= best_len
        else:
            tokens.append(('L', data[w]))
            if len(key) == 3:
                chain[w] = head.get(key)
                head[key] = w
            w -= 1

    cap = len(tokens) * 2 + len(tokens) // 8 + 16
    buf = bytearray(cap)
    pos = cap - 1
    for gi in range(0, len(tokens), 8):
        group = tokens[gi:gi + 8]
        fb = 0
        for i, t in enumerate(group):
            if t[0] == 'R':                    # bit 1 = back-reference
                fb |= 1 << (7 - i)
        buf[pos] = fb; pos -= 1
        for t in group:
            if t[0] == 'L':
                buf[pos] = t[1]; pos -= 1
            else:
                buf[pos] = t[1]; pos -= 1      # b1 at the higher address
                buf[pos] = t[2]; pos -= 1      # b2 below it
    return bytes(buf[pos + 1:])


# ------------------------------------------------------------------ images
class ArmImage:
    """Readable/editable representation of one ARM binary.

    For ARM9, ``data`` is the decompressed body (base 0x0206F26C) and ``stub``
    is the first 0x4000 bytes.  For ARM7, ``data`` is the whole raw binary.
    """

    def __init__(self, name, path, stub, data, base, footer=None):
        self.name = name
        self.path = path
        self.stub = bytearray(stub)
        self.data = bytearray(data)            # editable bytes
        self.original = bytes(data)
        self.base = base
        self.footer = footer                   # ARM9 only: dict with packed/raw sizes
        self.entry = None

    def dirty(self):
        return bytes(self.data) != self.original

    def read_u8(self, off):
        return self.data[off]

    def write_u8(self, off, value):
        self.data[off] = value & 0xFF

    def write_bytes(self, off, values):
        for i, b in enumerate(values):
            self.data[off + i] = b & 0xFF
        return len(values)

    def save(self, path=None):
        path = path or self.path
        if self.name == 'arm7':
            open(path, 'wb').write(bytes(self.data))
            return len(self.data)
        if self.name != 'arm9':
            raise ArmError('unknown image %s' % self.name)
        # Keep the binary the same size: stream starts at the top of the original
        # stream region, source start in the footer points exactly at it.
        stream = lzss_compress(bytes(self.data))
        orig_stream_end = self.footer['footer_end'] - ARM9_FOOTER_SIZE
        orig_stream_start = self.footer['stream_start']
        if len(stream) > orig_stream_end - orig_stream_start:
            raise ArmError('patched ARM9 stream grew past its slot (%d > %d bytes)'
                           % (len(stream), orig_stream_end - orig_stream_start))
        new_start = orig_stream_end - len(stream)
        out = bytearray(self.stub)
        out += b'\xFF' * (new_start - ARM9_STUB)
        out += stream
        out += b'\xFF' * ARM9_GAP
        packed = ((ARM9_FOOTER_SIZE << 24) |
                  (len(stream) + ARM9_FOOTER_SIZE))
        out += struct.pack('<II', packed, len(self.data))
        assert len(out) == self.footer['file_size'], (len(out), self.footer['file_size'])
        open(path, 'wb').write(bytes(out))
        return len(out)


def load(work):
    """Read both ARM binaries from an unpacked work folder."""
    images = {}
    p9 = os.path.join(work, '_arm9.bin')
    if os.path.isfile(p9):
        raw = open(p9, 'rb').read()
        if len(raw) < ARM9_STUB + FOOTER_LEN + ARM9_FOOTER_SIZE:
            raise ArmError('_arm9.bin is too small')
        footer_end = len(raw)
        packed, out_size = struct.unpack_from('<II', raw, footer_end - FOOTER_LEN)
        comp_size = packed & 0x00FFFFFF
        stream_start = footer_end - comp_size
        body, left, ptr = lzss_decompress(raw, stream_start, footer_end, out_size)
        if left:
            raise ArmError('ARM9 LZSS decode incomplete (%d bytes left)' % left)
        info = dict(footer_end=footer_end, stream_start=stream_start,
                    file_size=len(raw), packed=packed, out_size=out_size,
                    decoded_end_ptr=ptr)
        img = ArmImage('arm9', p9, raw[:ARM9_STUB], body, ARM9_BODY_BASE, info)
        img.entry = ARM9_BODY_BASE
        images['arm9'] = img
    p7 = os.path.join(work, '_arm7.bin')
    if os.path.isfile(p7):
        raw = open(p7, 'rb').read()
        img = ArmImage('arm7', p7, b'', raw, ARM7_BASE)
        img.entry = ARM7_BASE
        images['arm7'] = img
    return images


# ------------------------------------------------------------------ disassembly
def _labels(image):
    """Collect branch targets and literal-pool addresses as labels."""
    data = bytes(image.data)
    base = image.base
    labels = {base + image.entry: 'entry'} if image.entry is not None else {}
    if capstone is None:
        return labels
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    md.skipdata = True
    for ins in md.disasm(data, base):
        if ins.mnemonic.startswith('b') and ins.op_str.startswith('#'):
            try:
                addr = int(ins.op_str[1:], 16)
                if base <= addr < base + len(data):
                    labels.setdefault(addr, 'loc_%X' % addr)
            except ValueError:
                pass
    return labels


def disassemble(image, max_lines=None):
    """A labelled ARM listing.  Data pools are shown as .word rows."""
    if capstone is None:
        raise ArmError('capstone is required for disassembly (pip install capstone)')
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    md.skipdata = True
    md.detail = True
    labels = _labels(image)
    data = bytes(image.data)
    base = image.base
    lines = ['; %s: %d bytes at 0x%08X' % (image.name, len(data), base)]
    if image.footer:
        lines.append('; stream 0x%X..0x%X, footer at ROM 0x%X, decompressed %d bytes' %
                     (image.footer['stream_start'],
                      image.footer['footer_end'] - ARM9_FOOTER_SIZE,
                      image.footer['footer_end'] - FOOTER_LEN, len(data)))
    lines.append('')
    count = 0
    for ins in md.disasm(data, base):
        addr = ins.address
        if addr in labels:
            lines.append('')
            lines.append('%08X  <%s>:' % (addr, labels[addr]))
        off = addr - base
        raw = data[off:off + 4]
        lines.append('%08X  %s %-8s %s' % (addr, raw[::-1].hex(), ins.mnemonic, ins.op_str))
        count += 1
        if max_lines and count >= max_lines:
            lines.append('; ... truncated ...')
            break
    return '\n'.join(lines)


# ------------------------------------------------------------------ repack
def repack_with_arm(rom, work, out, log=print):
    """Repack a ROM after ARM edits: patch the ARM binaries in the ROM image."""
    from nds_repack import crc16, repack as nds_repack

    images = load(work)
    # First let the normal repacker rebuild the filesystem, then patch ARM bytes
    # into that output (ARM binaries are not part of the FAT).
    nds_repack(rom, work, out, log=lambda *a: None)
    data = bytearray(open(out, 'rb').read())
    d = open(rom, 'rb').read()
    for name, img in images.items():
        # The ARM binaries are not part of the FAT, so nds_repack never copies
        # them.  Always write the work folder's version, changed or not.
        new = bytes(img.data) if name == 'arm7' else _build_arm9(img)
        off, _entry, _ram, size = struct.unpack_from(
            '<IIII', d, 0x20 if name == 'arm9' else 0x30)
        if len(new) > size:
            raise ArmError('%s grew (%d > %d); repack cannot extend it' %
                           (name, len(new), size))
        if data[off:off + size] == new + b'\xFF' * (size - len(new)):
            continue
        data[off:off + len(new)] = new
        data[off + len(new):off + size] = b'\xFF' * (size - len(new))
        log('patched %s at ROM 0x%X (%d bytes)' % (name, off, len(new)))
    struct.pack_into('<H', data, 0x15E, crc16(bytes(data[:0x15E])))
    open(out, 'wb').write(bytes(data))
    log('ARM-aware repack written to %s' % out)


def _build_arm9(img):
    """Rebuild an ARM9 binary of the original size from an edited image."""
    tmp = img.path + '.tmp'
    try:
        img.save(tmp)
        return open(tmp, 'rb').read()
    finally:
        if os.path.isfile(tmp):
            os.remove(tmp)


def rom_info(rom):
    d = open(rom, 'rb').read(0x200)
    out = {}
    for name, off in (('arm9', 0x20), ('arm7', 0x30)):
        o, e, ram, size = struct.unpack_from('<IIII', d, off)
        out[name] = dict(rom_offset=o, entry=e, ram=ram, size=size)
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    cmd, work = sys.argv[1], sys.argv[2]
    images = load(work)
    if cmd == 'info':
        for n, img in images.items():
            print('%s: %d readable bytes at 0x%08X%s' %
                  (n, len(img.data), img.base, ' (dirty)' if img.dirty() else ''))
    elif cmd == 'test':
        img = images.get('arm9')
        if not img:
            print('no arm9 image')
            return 1
        stream = lzss_compress(bytes(img.original))
        footer_end = img.footer['footer_end']
        buf = bytearray(footer_end)
        buf[footer_end - ARM9_FOOTER_SIZE - len(stream):
            footer_end - ARM9_FOOTER_SIZE] = stream
        body, left, _p = lzss_decompress(
            buf, footer_end - ARM9_FOOTER_SIZE - len(stream),
            footer_end, len(img.original))
        print('LZSS round-trip:', 'OK' if not left and body == bytes(img.original)
              else 'FAILED (%d bytes left)' % left)
        print('compressed %d -> %d bytes' % (len(img.original), len(stream)))
    elif cmd == 'dis':
        which = sys.argv[3] if len(sys.argv) > 3 else 'arm9'
        img = images[which]
        text = disassemble(img)
        if len(sys.argv) > 4:
            open(sys.argv[4], 'w', encoding='utf-8').write(text)
            print('wrote', sys.argv[4])
        else:
            print(text[:4000])
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
