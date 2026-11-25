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

def load_records(tex_path):
    """Read Menus/menu.bin: 20-byte records at 0x340 (stride 20).

    Each record is {u32 block_offset, u16 pal_bytes, u16 f3, u16 f4, ...,
    u16 usize, ...} with `pal_bytes + f3 + f4 == usize`.  f3 is the real
    index-byte count of the block's main image; f4 is a separate trailing
    strip (present on the large menu screens, absent on portraits).
    """
    recs = {}
    binpath = os.path.join(os.path.dirname(tex_path), 'menu.bin')
    if not os.path.isfile(binpath):
        return recs
    d = open(binpath, 'rb').read()
    for s in range(0x340, len(d) - 20 + 1, 20):
        off = struct.unpack_from('<I', d, s)[0]
        u = struct.unpack_from('<10H', d, s)
        if u[2] == 512 and 512 + u[3] + u[4] == u[6]:
            recs[off] = dict(f3=u[3], f4=u[4], usize=u[6], rec_off=s)
    return recs


def blocks(path):
    data = open(path, "rb").read()
    recs = load_records(path)
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
                   data_end=end, next=nxt, padding=nxt - end,
                   rec=recs.get(pos))
        pos = nxt; index += 1

def parse_image(payload, rec=None):
    if len(payload) < 512:
        return None
    palette = list(struct.unpack_from("<256H", payload, 0))
    n = rec["f3"] if rec else len(payload) - 512
    idx = payload[512:512 + n]
    return palette, idx

def guess_width(n, rec=None, preferred=(64, 128, 192, 256, 320, 384, 512, 160, 32)):
    """Return (width, height) for an index count n.

    The large menu screens have their height in the menu.bin record's f4:
    f3 / 192 (height 192) or, when that does not divide, f3 / 128.  Small
    portraits/icons fall back to the old assumption (64x64, 64x32, ...).
    """
    if rec:
        ms = map_size(rec)
        if ms:
            return ms
        for ht in (192, 128):
            if n % ht == 0 and n // ht >= 64:
                return n // ht, ht
    for w in preferred:
        h = n // w if n % w == 0 else 0
        if h and h % 8 == 0 and 1/4 <= w/h <= 4:
            return w, h
    for w in range(8, 1025, 8):
        h = n // w if n % w == 0 else 0
        if h and h % 8 == 0 and 1/4 <= w/h <= 4 and w >= 64:
            return w, h
    return None, None

def map_size(rec):
    """Large blocks have a u16 tilemap: 32 tiles wide (256 px), f4/2/32 rows."""
    if not rec or not rec.get('f4'):
        return None
    rows = rec['f4'] // 2 // 32
    return 256, rows * 8 if rows else None


def render_mapped(payload, rec, palette=None):
    """Assemble a tileset + u16 tilemap (32 tiles wide) into a PIL Image."""
    from PIL import Image
    f3, f4 = rec['f3'], rec['f4']
    pal = palette if palette is not None else struct.unpack_from('<256H', payload, 0)
    tileset = payload[512:512 + f3]
    rows = f4 // 2 // 32
    if not rows:
        return None
    entries = struct.unpack_from('<%dH' % (32 * rows), payload, 512 + f3)
    ntile = len(tileset) // 64
    im = Image.new('RGB', (256, rows * 8))
    px = im.load()
    for i, t in enumerate(entries):
        if t >= ntile:
            continue
        tx, ty = (i % 32) * 8, (i // 32) * 8
        tile = tileset[t * 64:(t + 1) * 64]
        for y in range(8):
            for x in range(8):
                px[tx + x, ty + y] = rgb555(pal[tile[y * 8 + x]])
    return im


def rgb555(c):
    return ((c & 31) * 255 // 31, ((c >> 5) & 31) * 255 // 31, ((c >> 10) & 31) * 255 // 31)

def to_image(payload, width=None, height=None, rec=None):
    """Decode a block payload to a PIL Image (8x8 tiles detiled)."""
    from PIL import Image
    pal, idx = parse_image(payload, rec)
    if not pal:
        return None
    if rec and rec.get('f4') and width is None:
        return render_mapped(payload, rec, pal)
    if width is None:
        width, height = guess_width(len(idx), rec)
    if not width or len(idx) % width:
        return None
    if height is None:
        height = len(idx) // width
    if width < 8 or height < 8:
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
    return im


def save_png(payload, path, width=None, height=None, rec=None):
    im = to_image(payload, width, height, rec)
    if im is None:
        return None
    im.save(path)
    return im.size

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
