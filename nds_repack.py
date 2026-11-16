#!/usr/bin/env python3
"""Rebuild a .nds from the original ROM + an extracted folder (see nds_unpack.py).
Files that fit their original slot are patched in place; larger files are appended
to the end of the ROM and the FAT is updated. Header CRC and ROM size are fixed up.
usage: nds_repack.py <original.nds> <extracted_dir> <out.nds>"""
import sys, os, struct
from nds_unpack import read_nds

def crc16(data, crc=0xFFFF):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

def repack(orig, folder, out, log=print):
    d, fat, files = read_nds(orig)
    rom = bytearray(d)
    fat_off = struct.unpack('<I', d[0x48:0x4c])[0]
    # slot end for each file = start of the next thing in the ROM
    starts = sorted(set(s for s, e in fat) | {len(d)})
    end = len(rom)
    changed = moved = 0
    for name, fid in sorted(files.items(), key=lambda kv: kv[1]):
        s, e = fat[fid]
        p = os.path.join(folder, name)
        new = open(p, 'rb').read()
        if new == d[s:e]:
            continue
        changed += 1
        nxt = min([x for x in starts if x > s] or [len(d)])
        if len(new) <= nxt - s:
            rom[s:s + len(new)] = new
            if len(new) < e - s:
                rom[s + len(new):e] = b'\xFF' * (e - s - len(new))
            ns, ne = s, s + len(new)
        else:
            end = (end + 0x1FF) & ~0x1FF
            if len(rom) < end:
                rom += b'\xFF' * (end - len(rom))
            ns, ne = end, end + len(new)
            rom += new
            end = ne; moved += 1
        struct.pack_into('<II', rom, fat_off + fid * 8, ns, ne)
    # header fix-ups
    total = (len(rom) + 3) & ~3
    rom += b'\xFF' * (total - len(rom))
    used = struct.unpack('<I', d[0x80:0x84])[0]
    if moved:
        struct.pack_into('<I', rom, 0x80, max(used, end))
    cap = 0
    while (0x20000 << cap) < total: cap += 1
    rom[0x14] = max(rom[0x14], cap)
    struct.pack_into('<H', rom, 0x15E, crc16(bytes(rom[:0x15E])))
    open(out, 'wb').write(rom)
    log('%d files changed (%d appended), %d bytes written to %s' % (changed, moved, len(rom), out))

if __name__ == '__main__':
    repack(*sys.argv[1:4])
