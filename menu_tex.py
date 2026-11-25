"""
menu.tex format decoder (prototype, research only).

Verified by decoding every block of
C:\\Users\\Heumanyoung\\Documents\\gera-tools\\work\\Menus\\menu.tex
and by rendering the resulting 8-bpp tiled images (character portraits,
weapon icons and menu screens with readable text).

Usage:
    python tex_format.py <menu.tex> [--dump DIR] [--png DIR]

Format summary
--------------
The file is a concatenation of 4-byte-aligned LZSS blocks.  There is no
global header, no magic and no section table.

Block header (4 bytes):
    u8  0x10                 (always)
    u24 le  uncompressed_size

LZSS stream (starts at header+4):
    flag byte, bits consumed MSB first (bit7 .. bit0):
      bit = 0 -> literal byte
      bit = 1 -> 2-byte back reference:
          a = first byte,  b = second byte
          count = (a >> 4) + 3
          distance = ((a & 0x0F) << 8 | b) + 1   (backwards from output head)
    Decoding stops when uncompressed_size output bytes have been produced.
    The next block starts at the next 4-byte boundary.

Decompressed block payload (a palettised 8-bpp image):
    u16[256] palette         512 bytes, little-endian RGB555
    u8[]     indices         uncompressed_size - 512 bytes
    Pixels are stored as 8x8 tiles in raster tile order.  The image width
    is NOT stored in the .tex block; it must come from context (menu.bin)
    or be guessed.  Known widths are multiples of 8 (64, 128, 192, ...).
"""
import os, sys, struct, glob

MAGIC = 0x10

def lzss_decompress(data, pos, size):
    out = bytearray()
    src = pos
    while len(out) < size and src < len(data):
        flags = data[src]; src += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if flags & (1 << bit):
                if src + 1 >= len(data):
                    return bytes(out), src, "truncated reference"
                a, b = data[src], data[src + 1]; src += 2
                count = (a >> 4) + 3
                distance = ((a & 0x0F) << 8 | b) + 1
                if distance > len(out):
                    return bytes(out), src, "bad distance %d at output %d" % (distance, len(out))
                for _ in range(count):
                    out.append(out[-distance])
            else:
                if src >= len(data):
                    return bytes(out), src, "truncated literal"
                out.append(data[src]); src += 1
    return bytes(out), src, "ok" if len(out) == size else "short output"

def blocks(path):
    data = open(path, "rb").read()
    pos = 0
    index = 0
    while pos + 4 <= len(data):
        if data[pos] != MAGIC:
            raise ValueError("no LZSS header at 0x%X" % pos)
        size = data[pos + 1] | (data[pos + 2] << 8) | (data[pos + 3] << 16)
        payload, end, status = lzss_decompress(data, pos + 4, size)
        if status != "ok":
            raise ValueError("block %d at 0x%X: %s" % (index, pos, status))
        nxt = (end + 3) & ~3
        if nxt >= len(data):
            nxt = len(data)
        yield dict(index=index, offset=pos, usize=size, payload=payload,
                   data_end=end, next=nxt, padding=nxt - end)
        pos = nxt; index += 1

def parse_image(payload):
    if len(payload) < 512:
        return None
    palette = list(struct.unpack_from("<256H", payload, 0))
    idx = payload[512:]
    return palette, idx

def guess_width(n, preferred=(64, 128, 192, 256, 320, 384, 512, 160, 32)):
    """Widths are not stored in the .tex block.  Return a width only when it
    gives a plausible image (both dimensions multiples of 8, aspect <= 4:1)."""
    for w in preferred:
        h = n // w if n % w == 0 else 0
        if h and h % 8 == 0 and 1/4 <= w/h <= 4:
            return w, h
    for w in range(8, 1025, 8):
        h = n // w if n % w == 0 else 0
        if h and h % 8 == 0 and 1/4 <= w/h <= 4 and w >= 64:
            return w, h
    return None, None

def rgb555(c):
    return ((c & 31) * 255 // 31, ((c >> 5) & 31) * 255 // 31, ((c >> 10) & 31) * 255 // 31)

def save_png(payload, path, width=None):
    from PIL import Image
    pal, idx = parse_image(payload)
    if width is None:
        width, _ = guess_width(len(idx))
    if not width or len(idx) % width:
        return None
    height = len(idx) // width
    if width % 8 or height % 8:
        return None
    im = Image.new("RGB", (width, height))
    px = im.load()
    n = 0
    for ty in range(height // 8):
        for tx in range(width // 8):
            for y in range(8):
                for x in range(8):
                    v = idx[n]; n += 1
                    px[tx * 8 + x, ty * 8 + y] = rgb555(pal[v])
    im.save(path)
    return width, height

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__); return
    src = args[0]
    dump = None; png = None
    if "--dump" in sys.argv:
        dump = sys.argv[sys.argv.index("--dump") + 1]
        os.makedirs(dump, exist_ok=True)
    if "--png" in sys.argv:
        png = sys.argv[sys.argv.index("--png") + 1]
        os.makedirs(png, exist_ok=True)
    total = 0
    for b in blocks(src):
        total += 1
        img = parse_image(b["payload"])
        w = h = None
        if img:
            w, h = guess_width(len(img[1]))
        print("#%03d off=%08X csize=%5d usize=%6d pad=%d palette=%s idx=%s guess=%sx%s" % (
            b["index"], b["offset"], b["next"] - b["offset"], b["usize"], b["padding"],
            "yes" if img else "NO",
            len(img[1]) if img else 0, w, h))
        if dump:
            open(os.path.join(dump, "blk_%03d_%08X.bin" % (b["index"], b["offset"])), "wb").write(b["payload"])
        if png and img:
            wh = save_png(b["payload"], os.path.join(png, "img_%03d.png" % b["index"]))
            if wh:
                print("    PNG %dx%d" % wh)
    print("total blocks:", total)

if __name__ == "__main__":
    main()
