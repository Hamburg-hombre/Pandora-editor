#!/usr/bin/env python3
"""Minimal NDS NitroFS unpacker + file survey."""
import struct, sys, os, collections

def read_nds(path):
    d = open(path, 'rb').read()
    fnt_off, fnt_size, fat_off, fat_size = struct.unpack('<IIII', d[0x40:0x50])
    fat = [struct.unpack('<II', d[fat_off+i*8:fat_off+i*8+8]) for i in range(fat_size//8)]
    # directory table
    dirs = {}
    root_cnt = struct.unpack('<H', d[fnt_off+6:fnt_off+8])[0]
    def dir_entry(i):
        o = fnt_off + i*8
        return struct.unpack('<IHH', d[o:o+8])  # sub_off, first_file, parent
    files = {}  # path -> id
    def walk(dir_id, prefix):
        sub_off, first_file, _ = dir_entry(dir_id & 0xFFF)
        p = fnt_off + sub_off
        fid = first_file
        while True:
            t = d[p]; p += 1
            if t == 0: break
            ln = t & 0x7F
            name = d[p:p+ln].decode('latin1'); p += ln
            if t & 0x80:
                sub_id = struct.unpack('<H', d[p:p+2])[0]; p += 2
                walk(sub_id, prefix + name + '/')
            else:
                files[prefix + name] = fid; fid += 1
    walk(0xF000, '')
    return d, fat, files

def unpack(rom, out, log=print):
    d, fat, files = read_nds(rom)
    log("%d named files, %d FAT entries" % (len(files), len(fat)))
    exts = collections.Counter()
    for name, fid in files.items():
        s_, e_ = fat[fid]
        dst = os.path.join(out, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, 'wb').write(d[s_:e_])
        exts[os.path.splitext(name)[1].lower()] += 1
    log("extensions: " + ", ".join("%s x%d" % kv for kv in exts.most_common(12)))
    a9o, _, _, a9s = struct.unpack('<IIII', d[0x20:0x30])
    a7o, _, _, a7s = struct.unpack('<IIII', d[0x30:0x40])
    open(os.path.join(out, '_arm9.bin'), 'wb').write(d[a9o:a9o+a9s])
    open(os.path.join(out, '_arm7.bin'), 'wb').write(d[a7o:a7o+a7s])
    log("done -> " + out)

def main():
    unpack(sys.argv[1], sys.argv[2])

if __name__ == "__main__":
    main()
