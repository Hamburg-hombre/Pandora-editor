"""Help Hint Panel text stored in Templates.CRT.

Fort Knox's STR_HINT1..11 placements are Help Hint Panels (w1 0x3973BB0B).
Each hint number selects an entry in an 8-byte pointer table at 0x521C:

    u32 textOffset = value - 0x8C74     (start of the NUL-terminated text)
    u32 nextOffset                      (start of the following string)

When the new text fits, it is overwritten in the slot it currently points to.
When it is longer, the original text is left untouched, the new text is
appended at EOF, and the pointer entry is redirected to it.  The game then
shows the full message.
"""
import struct

HINT_PTR_TABLE = 0x521C
HINT_PTR_BASE = 0x8C74
HINT_PTR_STRIDE = 8
HINT_COUNT = 19


# The DS text renderer wraps at a fixed pixel width.  30 x 'j' or 9 x 'w'
# was reported to crash the game, so those widths define the limit (in 'j'
# units).  A word without spaces cannot be wrapped, hence the warning.
LINE_WIDTH = 28.0
_CHAR_WIDTH = {}


def _char_width(c):
    if c not in _CHAR_WIDTH:
        if c in 'jl.,;:\'"!|':
            w = 1.0
        elif c in 'wWM':
            w = 3.4
        elif c in 'mfOQ@':
            w = 2.8
        elif c in 'abcdefghknopqrstuvxyz':
            w = 2.0
        elif c in 'iItfr()[]-':
            w = 1.4
        elif c.isupper() or c.isdigit():
            w = 2.3
        else:
            w = 2.0
        _CHAR_WIDTH[c] = w
    return _CHAR_WIDTH[c]


def too_long_words(text):
    """Return whitespace-free words estimated to exceed one rendered line."""
    out = []
    for word in str(text).split():
        width = sum(_char_width(c) for c in word)
        if width > LINE_WIDTH:
            out.append((word, round(width, 1)))
    return out


def _entry(number):
    if not (1 <= number <= HINT_COUNT):
        return None
    return HINT_PTR_TABLE + HINT_PTR_STRIDE * (number - 1)


def hint_offset(crt, number):
    ent = _entry(number)
    if ent is None or ent + 4 > len(crt):
        return None
    off = HINT_PTR_BASE + struct.unpack_from('<I', crt, ent)[0]
    return off if 0 <= off < len(crt) else None


def hint_strings(crt):
    """Return [(file offset, text)] for the 19 hints, via the pointer table."""
    out = []
    for n in range(1, HINT_COUNT + 1):
        off = hint_offset(crt, n)
        if off is None:
            break
        end = crt.find(b'\x00', off)
        if end < 0 or end - off > 4000:
            break
        out.append((off, crt[off:end].decode('latin-1').rstrip()))
    return out


def read_hint(crt, number, overrides=None):
    items = hint_strings(crt)
    if 1 <= number <= len(items):
        return items[number - 1][1]
    return ''


def hint_capacity(crt, number):
    """Maximum characters writable without moving the string block."""
    off = hint_offset(crt, number)
    if off is None:
        return 0
    end = crt.find(b'\x00', off)
    return end - off


def write_hint(crt, number, text):
    """Write hint `number`; return (new_crt_bytes, appended_offset_or_None).

    Short text is overwritten in place.  Long text is appended at EOF and the
    hint's pointer entry is redirected to it.
    """
    ent = _entry(number)
    off = hint_offset(crt, number)
    if ent is None or off is None:
        raise ValueError('no hint %d in Templates.CRT' % number)
    blob = str(text).encode('latin-1')
    if b'\x00' in blob:
        raise ValueError('hint text cannot contain NUL bytes')
    data = bytearray(crt)
    end = crt.find(b'\x00', off)
    appended = None
    if len(blob) <= end - off:
        data[off:end] = blob + b' ' * (end - off - len(blob))
    else:
        appended = len(data)
        data += blob + b'\x00'
        rel = appended - HINT_PTR_BASE
        struct.pack_into('<II', data, ent, rel, rel + len(blob) + 1)
    return bytes(data), appended
